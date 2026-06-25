from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from src.company_context import company_context_for_name, load_company_context_map
from src.dashboard import apply_dashboard_and_digest
from src.models import JobPosting, normalize_key_part, utc_now_iso
from src.scoring import load_scoring_rules, score_job
from src.settings import load_settings
from src.sheets import SheetClient

OPEN_STATUSES = {"open", "reopened"}
GMAIL_SOURCE = "gmail_alert"


@dataclass(slots=True)
class RescoreJobsResult:
    jobs_read: int = 0
    jobs_selected: int = 0
    jobs_skipped: int = 0
    gmail_open_jobs: int = 0
    jobs_updated: int = 0
    jobs_would_update: int = 0
    manual_review_jobs: int = 0
    provisional_jobs: int = 0
    partially_verified_jobs: int = 0
    verified_jobs: int = 0
    excluded_jobs: int = 0
    high_potential_jobs: int = 0
    enrichment_pending_jobs: int = 0
    dashboard_refreshed: bool = False
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_open_gmail_job(job: JobPosting) -> bool:
    return job.source_primary.strip().lower() == GMAIL_SOURCE and job.status in OPEN_STATUSES


def _increment_state_counts(result: RescoreJobsResult, job: JobPosting) -> None:
    if "manual_review=true" in str(job.score_explanation or "").lower():
        result.manual_review_jobs += 1
    if job.score_status == "provisional":
        result.provisional_jobs += 1
    elif job.score_status == "partially_verified":
        result.partially_verified_jobs += 1
    elif job.score_status == "verified":
        result.verified_jobs += 1
    elif job.score_status == "excluded":
        result.excluded_jobs += 1
    if job.potential_priority == "high":
        result.high_potential_jobs += 1
    if job.enrichment_status == "pending":
        result.enrichment_pending_jobs += 1


def build_rescore_run_record(result: RescoreJobsResult, *, gmail_only: bool = False) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"{'sprint26_gmail_rescore' if gmail_only else 'sprint30_rescore'}_{run_timestamp}",
        "run_type": "sprint_22_sparse_gmail_rescore" if gmail_only else "sprint_30_verified_rescore",
        "source_type": GMAIL_SOURCE if gmail_only else "jobs",
        "source_name": "Open Gmail jobs" if gmail_only else "Selected Jobs rows",
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": result.jobs_selected,
        "records_inserted": 0,
        "records_updated": result.jobs_updated,
        "records_failed": 0,
        "rows_read": result.jobs_read,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(result.to_dict(), sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def _matches_company(job: JobPosting, company: str | None) -> bool:
    if not company:
        return True
    return normalize_key_part(job.company) == normalize_key_part(company)


def _selected(
    job: JobPosting,
    *,
    provisional_only: bool,
    verified_only: bool,
    job_key: str | None,
    company: str | None,
    all_open: bool,
    gmail_only: bool,
) -> bool:
    if job_key and job.job_key != job_key:
        return False
    if not _matches_company(job, company):
        return False
    if gmail_only and not _is_open_gmail_job(job):
        return False
    if not job_key and job.status not in OPEN_STATUSES:
        return False
    if all_open and job.status not in OPEN_STATUSES:
        return False
    if provisional_only and job.score_status != "provisional":
        return False
    if verified_only and job.score_status != "verified":
        return False
    return True


def rescore_jobs(
    sheet_client: SheetClient,
    scoring_rules: dict[str, Any],
    *,
    provisional_only: bool = False,
    verified_only: bool = False,
    job_key: str | None = None,
    company: str | None = None,
    all_open: bool = False,
    gmail_only: bool = False,
    dry_run: bool = False,
    refresh_dashboard: bool = False,
    append_run: bool = True,
) -> RescoreJobsResult:
    if provisional_only and verified_only:
        raise ValueError("provisional_only and verified_only cannot both be true")

    rows = sheet_client.read_jobs_with_row_numbers()
    company_contexts = load_company_context_map(sheet_client)
    result = RescoreJobsResult(jobs_read=len(rows), dry_run=dry_run)

    for row_number, job in rows:
        if _is_open_gmail_job(job):
            result.gmail_open_jobs += 1
        if not _selected(
            job,
            provisional_only=provisional_only,
            verified_only=verified_only,
            job_key=job_key,
            company=company,
            all_open=all_open,
            gmail_only=gmail_only,
        ):
            result.jobs_skipped += 1
            continue

        result.jobs_selected += 1
        scored = score_job(
            job,
            scoring_rules,
            company_context=company_context_for_name(job.company, company_contexts),
        )
        result.jobs_would_update += 1
        if not dry_run:
            sheet_client.update_job(row_number, scored)
            result.jobs_updated += 1
        _increment_state_counts(result, scored)

    if refresh_dashboard and not dry_run:
        apply_dashboard_and_digest(sheet_client, append_run=False)
        result.dashboard_refreshed = True

    if append_run and not dry_run:
        sheet_client.append_run(build_rescore_run_record(result, gmail_only=gmail_only))

    return result


def rescore_open_gmail_jobs(
    sheet_client: SheetClient,
    scoring_rules: dict[str, Any],
    *,
    refresh_dashboard: bool = True,
    append_run: bool = True,
) -> RescoreJobsResult:
    """Backward-compatible wrapper for the Sprint 26 Gmail-only rescore."""
    return rescore_jobs(
        sheet_client,
        scoring_rules,
        gmail_only=True,
        refresh_dashboard=refresh_dashboard,
        append_run=append_run,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-score Jobs with Sprint 30 verified-scoring requirements")
    state_group = parser.add_mutually_exclusive_group()
    state_group.add_argument("--provisional-only", action="store_true", help="Only re-score provisional jobs")
    state_group.add_argument("--verified-only", action="store_true", help="Only re-score verified jobs")
    parser.add_argument("--job-key", help="Only re-score the exact job_key")
    parser.add_argument("--company", help="Only re-score jobs for the normalized company name")
    parser.add_argument("--all-open", action="store_true", help="Re-score every open or reopened job")
    parser.add_argument("--dry-run", action="store_true", help="Calculate changes without writing Jobs, Runs, Dashboard, or Digest")
    parser.add_argument("--refresh-dashboard", action="store_true", help="Refresh Dashboard and Digest after successful writes")
    parser.add_argument("--no-run-log", action="store_true", help="Do not append a rescore record to Runs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    rules = load_scoring_rules(settings.scoring_rules_path)
    sheet_client = SheetClient.from_settings(settings)
    result = rescore_jobs(
        sheet_client,
        rules,
        provisional_only=args.provisional_only,
        verified_only=args.verified_only,
        job_key=args.job_key,
        company=args.company,
        all_open=args.all_open or not any([args.job_key, args.company, args.provisional_only, args.verified_only]),
        dry_run=args.dry_run,
        refresh_dashboard=args.refresh_dashboard,
        append_run=not args.no_run_log,
    )
    print(json.dumps({"run_mode": "sprint_30_verified_rescore", "status": "success", **result.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
