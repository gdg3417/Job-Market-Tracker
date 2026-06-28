from __future__ import annotations

import argparse
import json
import re
from typing import Any

from src.enrichment.lifecycle import LifecycleRunSummary, run_lifecycle_checks
from src.models import JobPosting, utc_now_iso
from src.production_readiness import LifecycleCadencePolicy, lifecycle_due_rows

TARGET_PRIORITY_TERMS = {"tier 1", "tier 2", "target", "watchlist", "high"}


def _identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _read_optional_records(sheet_client: Any, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return list(sheet_client.read_records(worksheet_name))
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def target_company_keys(rows: list[dict[str, Any]] | None) -> set[str]:
    keys: set[str] = set()
    for row in rows or []:
        if not _truthy(row.get("active"), default=True):
            continue
        priority = _identity(row.get("priority_tier"))
        boost = _safe_int(row.get("score_boost_points"), 0)
        if priority in TARGET_PRIORITY_TERMS or priority.startswith("tier 1") or priority.startswith("tier 2") or boost > 0:
            company = str(row.get("company_name") or row.get("parent_company") or "").strip()
            if company:
                keys.add(_identity(company))
    return keys


def build_priority_lifecycle_plan(
    jobs: list[JobPosting],
    *,
    target_keys: set[str] | None = None,
    now: str | None = None,
    limit: int = 50,
    policy: LifecycleCadencePolicy | None = None,
) -> list[dict[str, Any]]:
    return lifecycle_due_rows(
        jobs,
        now=now,
        target_company_keys=target_keys or set(),
        policy=policy,
        limit=limit,
    )


def preview_priority_lifecycle(sheet_client: Any, *, now: str | None = None, limit: int = 50) -> dict[str, Any]:
    jobs = [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    targets = target_company_keys(_read_optional_records(sheet_client, "Target_Companies"))
    plan = build_priority_lifecycle_plan(jobs, target_keys=targets, now=now, limit=limit)
    return {
        "run_mode": "sprint_38_priority_lifecycle_preview",
        "jobs_read": len(jobs),
        "due_jobs": len(plan),
        "jobs": plan,
    }


def _merge_summary(total: LifecycleRunSummary, summary: LifecycleRunSummary) -> None:
    total.jobs_evaluated += summary.jobs_evaluated
    total.jobs_checked += summary.jobs_checked
    total.jobs_updated += summary.jobs_updated
    total.jobs_unchanged += summary.jobs_unchanged
    total.temporary_failures += summary.temporary_failures
    total.likely_closed += summary.likely_closed
    total.confirmed_closed += summary.confirmed_closed
    total.expired += summary.expired
    total.reopened += summary.reopened
    total.open_confirmed += summary.open_confirmed
    total.evidence_written += summary.evidence_written
    total.queue_retries_scheduled += summary.queue_retries_scheduled
    total.queue_permanent_failures += summary.queue_permanent_failures
    total.duplicate_observations += summary.duplicate_observations
    total.health_metrics = summary.health_metrics or total.health_metrics


def build_priority_lifecycle_run_record(summary: LifecycleRunSummary, *, started_at: str, finished_at: str) -> dict[str, Any]:
    run_timestamp = finished_at.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint38_priority_lifecycle_{run_timestamp}",
        "run_type": "sprint_38_priority_lifecycle",
        "source_type": "jobs",
        "source_name": "Priority lifecycle scheduler",
        "status": "success",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": 0,
        "records_found": summary.jobs_evaluated,
        "records_inserted": summary.evidence_written,
        "records_updated": summary.jobs_updated,
        "records_failed": summary.temporary_failures,
        "rows_read": summary.jobs_evaluated,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary.to_dict(), sort_keys=True),
        "created_at": finished_at,
        "updated_at": finished_at,
    }


def run_priority_lifecycle(sheet_client: Any, *, now: str | None = None, limit: int = 50, append_run: bool = True) -> dict[str, Any]:
    started_at = now or utc_now_iso()
    preview = preview_priority_lifecycle(sheet_client, now=started_at, limit=limit)
    total = LifecycleRunSummary(jobs_evaluated=preview["due_jobs"])
    for row in preview["jobs"]:
        job_key = str(row.get("job_key") or "").strip()
        if not job_key:
            continue
        summary = run_lifecycle_checks(
            sheet_client,
            now=started_at,
            limit=1,
            job_key=job_key,
            write_run_record=False,
        )
        _merge_summary(total, summary)
    finished_at = started_at if now else utc_now_iso()
    if append_run and hasattr(sheet_client, "append_run"):
        sheet_client.append_run(build_priority_lifecycle_run_record(total, started_at=started_at, finished_at=finished_at))
    return {
        "run_mode": "sprint_38_priority_lifecycle",
        "status": "success",
        "planned_jobs": preview["due_jobs"],
        **total.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run priority-based lifecycle checks")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-run-log", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run and not args.run:
        raise SystemExit("Choose --dry-run or --run")
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise
    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    if args.dry_run:
        print(json.dumps(preview_priority_lifecycle(sheet_client, limit=args.limit), indent=2, sort_keys=True))
        return
    print(json.dumps(run_priority_lifecycle(sheet_client, limit=args.limit, append_run=not args.no_run_log), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
