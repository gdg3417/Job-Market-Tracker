from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from src.company_context import company_context_for_name, load_company_context_map
from src.dashboard import apply_dashboard_and_digest
from src.models import JobPosting, utc_now_iso
from src.scoring import load_scoring_rules, score_job
from src.settings import load_settings
from src.sheets import SheetClient

OPEN_STATUSES = {"open", "reopened"}
GMAIL_SOURCE = "gmail_alert"


@dataclass(slots=True)
class RescoreJobsResult:
    jobs_read: int = 0
    gmail_open_jobs: int = 0
    jobs_updated: int = 0
    manual_review_jobs: int = 0
    provisional_jobs: int = 0
    partially_verified_jobs: int = 0
    verified_jobs: int = 0
    excluded_jobs: int = 0
    high_potential_jobs: int = 0
    enrichment_pending_jobs: int = 0
    dashboard_refreshed: bool = False

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


def build_rescore_run_record(result: RescoreJobsResult) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint26_gmail_rescore_{run_timestamp}",
        "run_type": "sprint_22_sparse_gmail_rescore",
        "source_type": GMAIL_SOURCE,
        "source_name": "Open Gmail jobs",
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": result.gmail_open_jobs,
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


def rescore_open_gmail_jobs(
    sheet_client: SheetClient,
    scoring_rules: dict[str, Any],
    *,
    refresh_dashboard: bool = True,
    append_run: bool = True,
) -> RescoreJobsResult:
    rows = sheet_client.read_jobs_with_row_numbers()
    company_contexts = load_company_context_map(sheet_client)
    result = RescoreJobsResult(jobs_read=len(rows))

    for row_number, job in rows:
        if not _is_open_gmail_job(job):
            continue
        result.gmail_open_jobs += 1
        scored = score_job(
            job,
            scoring_rules,
            company_context=company_context_for_name(job.company, company_contexts),
        )
        sheet_client.update_job(row_number, scored)
        result.jobs_updated += 1
        _increment_state_counts(result, scored)

    if refresh_dashboard:
        apply_dashboard_and_digest(sheet_client, append_run=False)
        result.dashboard_refreshed = True

    if append_run:
        sheet_client.append_run(build_rescore_run_record(result))

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-score existing open Gmail jobs into Sprint 26 potential-priority and evidence states"
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Re-score Jobs without refreshing Dashboard and Digest",
    )
    parser.add_argument(
        "--no-run-log",
        action="store_true",
        help="Do not append a Sprint 26 record to Runs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    rules = load_scoring_rules(settings.scoring_rules_path)
    sheet_client = SheetClient.from_settings(settings)
    result = rescore_open_gmail_jobs(
        sheet_client,
        rules,
        refresh_dashboard=not args.no_refresh,
        append_run=not args.no_run_log,
    )
    print(json.dumps({"run_mode": "sprint_26_potential_priority_rescore", "status": "success", **result.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
