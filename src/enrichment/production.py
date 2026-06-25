from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.dashboard import apply_dashboard_and_digest
from src.enrichment.lifecycle import LifecycleRunSummary, lifecycle_health_metrics, run_lifecycle_checks
from src.enrichment.models import EnrichmentQueueItem, utc_now_iso
from src.enrichment.pipeline import run_enrichment_pipeline
from src.enrichment.search import DisabledSearchProvider, DuckDuckGoHtmlSearchProvider, SearchProvider
from src.models import JobPosting
from src.rescore_jobs import rescore_jobs


@dataclass(frozen=True, slots=True)
class ProductionLimits:
    direct_limit: int
    company_limit: int
    external_limit: int
    lifecycle_limit: int

    @classmethod
    def for_mode(cls, mode: str) -> "ProductionLimits":
        normalized = str(mode or "").strip().lower()
        if normalized == "daily":
            return cls(direct_limit=10, company_limit=10, external_limit=0, lifecycle_limit=0)
        if normalized == "weekly":
            return cls(direct_limit=10, company_limit=10, external_limit=5, lifecycle_limit=50)
        if normalized == "backfill":
            return cls(direct_limit=15, company_limit=15, external_limit=5, lifecycle_limit=50)
        raise ValueError(f"Unsupported production mode: {mode!r}")


@dataclass(slots=True)
class RecoverySummary:
    queue_rows_evaluated: int = 0
    stale_in_progress_found: int = 0
    queue_rows_recovered: int = 0
    jobs_recovered: int = 0
    direct_stage_recovered: int = 0
    company_stage_recovered: int = 0
    external_stage_recovered: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class ProductionRunSummary:
    mode: str
    started_at: str
    finished_at: str = ""
    recovery: dict[str, Any] = field(default_factory=dict)
    direct_link: dict[str, Any] = field(default_factory=dict)
    company_ats: dict[str, Any] = field(default_factory=dict)
    external_search: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    rescore: dict[str, Any] = field(default_factory=dict)
    dashboard: dict[str, Any] = field(default_factory=dict)
    health_metrics: dict[str, Any] = field(default_factory=dict)
    health_rows_written: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _records_with_rows(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    return [
        (index + 2, row)
        for index, row in enumerate(sheet_client.read_records(worksheet_name))
    ]


def _jobs_with_rows(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [
        (row_number, JobPosting.from_dict(record))
        for row_number, record in _records_with_rows(sheet_client, "Jobs")
    ]


def _update_job(sheet_client: Any, row_number: int, job: JobPosting) -> None:
    if hasattr(sheet_client, "update_job"):
        sheet_client.update_job(row_number, job)
    else:
        sheet_client.update_record("Jobs", row_number, job.to_dict())


def _recovery_state_for_stage(stage: str) -> tuple[str, str]:
    normalized = str(stage or "").strip().lower()
    if normalized == "company_ats":
        return "direct_url", "not_found"
    if normalized == "external_search":
        return "company_ats", "not_found"
    return "direct_url", "retryable_failure"


def recover_stale_in_progress(
    sheet_client: Any,
    *,
    now: str | None = None,
    stale_after_minutes: int = 90,
    job_key: str = "",
) -> RecoverySummary:
    timestamp = now or utc_now_iso()
    current = _parse_timestamp(timestamp)
    if current is None:
        raise ValueError(f"Invalid recovery timestamp: {timestamp!r}")
    cutoff = current - timedelta(minutes=max(0, stale_after_minutes))

    queue_rows = [
        (row_number, EnrichmentQueueItem.from_dict(record))
        for row_number, record in _records_with_rows(sheet_client, "Enrichment_Queue")
    ]
    eligible_queue_rows = [
        pair for pair in queue_rows if not job_key or pair[1].job_key == job_key
    ]
    jobs = {
        job.job_key: (row_number, job)
        for row_number, job in _jobs_with_rows(sheet_client)
        if job.job_key and (not job_key or job.job_key == job_key)
    }
    summary = RecoverySummary(queue_rows_evaluated=len(eligible_queue_rows))
    recovered_jobs: set[str] = set()

    for row_number, item in eligible_queue_rows:
        if item.status != "in_progress":
            continue
        last_change = _parse_timestamp(item.updated_at) or _parse_timestamp(item.last_attempted_at)
        if last_change is not None and last_change > cutoff:
            continue

        summary.stale_in_progress_found += 1
        prior_stage = item.current_stage
        item.current_stage, item.status = _recovery_state_for_stage(prior_stage)
        item.next_attempt_at = timestamp if item.status == "retryable_failure" else ""
        item.error_type = "interrupted_run"
        item.error_message = (
            f"Recovered stale in_progress work from {prior_stage or 'unknown'} after "
            f"{max(0, stale_after_minutes)} minutes"
        )
        item.updated_at = timestamp
        sheet_client.update_record("Enrichment_Queue", row_number, item.to_dict())
        summary.queue_rows_recovered += 1

        if prior_stage == "company_ats":
            summary.company_stage_recovered += 1
        elif prior_stage == "external_search":
            summary.external_stage_recovered += 1
        else:
            summary.direct_stage_recovered += 1

        target = jobs.get(item.job_key)
        if target is None:
            continue
        job_row, job = target
        job.enrichment_status = item.status
        job.enrichment_last_attempted_at = timestamp
        if hasattr(job, "refresh_updated_at"):
            job.refresh_updated_at()
        _update_job(sheet_client, job_row, job)
        recovered_jobs.add(item.job_key)

    summary.jobs_recovered = len(recovered_jobs)
    return summary


def write_enrichment_health_section(
    sheet_client: Any,
    health_metrics: dict[str, Any],
    *,
    generated_at: str,
) -> int:
    if not hasattr(sheet_client, "get_worksheet"):
        return 0
    from src.sheets import with_quota_backoff

    worksheet = sheet_client.get_worksheet("Dashboard")
    existing = with_quota_backoff(
        lambda: worksheet.get_all_values(),
        operation_name="read Dashboard before enrichment health write",
    )
    start_row = max(1, len(existing) + 2)
    rows = [
        ["Enrichment and lifecycle health"],
        ["Generated at", generated_at],
        ["Metric", "Value", "Meaning"],
        ["Enrichment backlog", health_metrics.get("enrichment_backlog", 0), "Open queue items that still require automated work"],
        ["Retryable failures", health_metrics.get("retryable_failures", 0), "Transient failures scheduled for another attempt"],
        ["Ambiguous matches", health_metrics.get("ambiguous_matches", 0), "Candidate postings that require manual review"],
        ["Jobs likely closed", health_metrics.get("jobs_likely_closed", 0), "Roles with supporting closure evidence"],
        ["Jobs confirmed closed", health_metrics.get("jobs_confirmed_closed", 0), "Roles closed or expired through authoritative evidence"],
        ["Oldest pending enrichment days", health_metrics.get("oldest_pending_enrichment_days", 0), "Age of the oldest pending queue item"],
        ["Average enrichment attempts", health_metrics.get("average_enrichment_attempts", 0), "Average attempts across enrichment queue rows"],
        ["Enrichment success rate percent", health_metrics.get("enrichment_success_rate_percent", 0), "Share of attempted queue rows enriched or partially enriched"],
    ]
    with_quota_backoff(
        lambda: worksheet.update(
            range_name=f"A{start_row}",
            values=rows,
            value_input_option="USER_ENTERED",
        ),
        operation_name="write Dashboard enrichment health section",
    )
    return len(rows)


def _build_run_record(summary: ProductionRunSummary) -> dict[str, Any]:
    started = _parse_timestamp(summary.started_at)
    finished = _parse_timestamp(summary.finished_at)
    duration = max(0, int((finished - started).total_seconds())) if started and finished else 0
    run_timestamp = summary.finished_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    direct = summary.direct_link
    company = summary.company_ats
    external = summary.external_search
    lifecycle = summary.lifecycle
    failures = (
        int(direct.get("retryable_failures") or 0)
        + int(direct.get("permanent_failures") or 0)
        + int(company.get("failures") or 0)
        + int(external.get("search_failures") or 0)
        + int(lifecycle.get("temporary_failures") or 0)
    )
    updated = (
        int(direct.get("jobs_updated") or 0)
        + int(company.get("jobs_updated") or 0)
        + int(external.get("jobs_updated") or 0)
        + int(lifecycle.get("jobs_updated") or 0)
        + int(summary.rescore.get("jobs_updated") or 0)
    )
    evaluated = max(
        int(direct.get("jobs_evaluated") or 0),
        int(summary.rescore.get("jobs_selected") or 0),
        int(lifecycle.get("jobs_evaluated") or 0),
    )
    rows_read = max(evaluated, int(summary.rescore.get("jobs_read") or 0))
    return {
        "run_id": f"sprint32_{summary.mode}_{run_timestamp}",
        "run_type": f"sprint_32_enrichment_{summary.mode}",
        "source_type": "jobs",
        "source_name": "Production enrichment pipeline",
        "status": "success",
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "duration_seconds": duration,
        "records_found": evaluated,
        "records_inserted": (
            int(direct.get("evidence_written") or 0)
            + int(company.get("evidence_written") or 0)
            + int(external.get("evidence_written") or 0)
            + int(lifecycle.get("evidence_written") or 0)
        ),
        "records_updated": updated,
        "records_failed": failures,
        "rows_read": rows_read,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary.to_dict(), sort_keys=True),
        "created_at": summary.finished_at,
        "updated_at": summary.finished_at,
    }


def run_production_cycle(
    sheet_client: Any,
    scoring_rules: dict[str, Any],
    *,
    mode: str = "daily",
    limits: ProductionLimits | None = None,
    search_provider: SearchProvider | None = None,
    now: str | None = None,
    stale_after_minutes: int = 90,
    job_key: str = "",
    append_run: bool = True,
) -> ProductionRunSummary:
    started_at = now or utc_now_iso()
    normalized_mode = str(mode or "").strip().lower()
    selected_limits = limits or ProductionLimits.for_mode(normalized_mode)
    provider = search_provider
    if provider is None:
        provider = (
            DuckDuckGoHtmlSearchProvider()
            if selected_limits.external_limit > 0
            else DisabledSearchProvider()
        )

    result = ProductionRunSummary(mode=normalized_mode, started_at=started_at)
    recovery = recover_stale_in_progress(
        sheet_client,
        now=started_at,
        stale_after_minutes=stale_after_minutes,
        job_key=job_key,
    )
    result.recovery = recovery.to_dict()

    pipeline = run_enrichment_pipeline(
        sheet_client,
        direct_limit=selected_limits.direct_limit,
        company_limit=selected_limits.company_limit,
        external_limit=selected_limits.external_limit,
        job_key=job_key,
        search_provider=provider,
        search_query_budget=0 if selected_limits.external_limit <= 0 else 5,
        search_results_per_query=5,
        candidate_page_budget=3,
    )
    result.direct_link = pipeline["direct_link"]
    result.company_ats = pipeline["company_ats"]
    result.external_search = pipeline["external_search"]

    if selected_limits.lifecycle_limit > 0:
        lifecycle = run_lifecycle_checks(
            sheet_client,
            now=started_at if now is not None else None,
            limit=selected_limits.lifecycle_limit,
            job_key=job_key,
            write_run_record=False,
        )
    else:
        lifecycle = LifecycleRunSummary()
    result.lifecycle = lifecycle.to_dict()

    rescore = rescore_jobs(
        sheet_client,
        scoring_rules,
        job_key=job_key or None,
        all_open=not bool(job_key),
        dry_run=False,
        refresh_dashboard=False,
        append_run=False,
    )
    result.rescore = rescore.to_dict()

    dashboard = apply_dashboard_and_digest(sheet_client, append_run=False)
    result.dashboard = dashboard.to_dict()

    jobs = [job for _, job in _jobs_with_rows(sheet_client)]
    queue = [
        EnrichmentQueueItem.from_dict(record)
        for _, record in _records_with_rows(sheet_client, "Enrichment_Queue")
    ]
    result.health_metrics = lifecycle_health_metrics(jobs, queue, now=started_at)
    result.health_rows_written = write_enrichment_health_section(
        sheet_client,
        result.health_metrics,
        generated_at=started_at,
    )
    result.finished_at = utc_now_iso() if now is None else started_at

    if append_run and hasattr(sheet_client, "append_run"):
        sheet_client.append_run(_build_run_record(result))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run production-safe daily, weekly, or controlled-backfill enrichment"
    )
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--run", action="store_true")
    execution.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("daily", "weekly", "backfill"), default="daily")
    parser.add_argument("--job-key", default="")
    parser.add_argument("--direct-limit", type=int)
    parser.add_argument("--company-limit", type=int)
    parser.add_argument("--external-limit", type=int)
    parser.add_argument("--lifecycle-limit", type=int)
    parser.add_argument("--stale-after-minutes", type=int, default=90)
    parser.add_argument("--no-run-log", action="store_true")
    return parser.parse_args()


def _overridden_limits(args: argparse.Namespace) -> ProductionLimits:
    defaults = ProductionLimits.for_mode(args.mode)
    return ProductionLimits(
        direct_limit=defaults.direct_limit if args.direct_limit is None else max(0, args.direct_limit),
        company_limit=defaults.company_limit if args.company_limit is None else max(0, args.company_limit),
        external_limit=defaults.external_limit if args.external_limit is None else max(0, args.external_limit),
        lifecycle_limit=defaults.lifecycle_limit if args.lifecycle_limit is None else max(0, args.lifecycle_limit),
    )


def main() -> None:
    args = parse_args()

    from src.enrichment.company_run import preview_company_ats_queue
    from src.enrichment.lifecycle import preview_lifecycle_checks
    from src.enrichment.run import preview_direct_link_queue
    from src.enrichment.search_run import preview_external_search_queue
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise
    from src.scoring import load_scoring_rules
    from src.settings import load_settings
    from src.sheets import SheetClient

    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    limits = _overridden_limits(args)

    if args.dry_run:
        validate_workbook_or_raise(sheet_client)
        preview = {
            "mode": args.mode,
            "limits": asdict(limits),
            "direct_link": preview_direct_link_queue(sheet_client, job_key=args.job_key),
            "company_ats": preview_company_ats_queue(sheet_client, job_key=args.job_key),
            "external_search": preview_external_search_queue(sheet_client, job_key=args.job_key),
            "lifecycle": preview_lifecycle_checks(sheet_client, job_key=args.job_key),
        }
        print(json.dumps(preview, indent=2))
        return

    migration = migrate_trailing_headers(sheet_client)
    if not migration.ok:
        raise RuntimeError("Workbook schema migration did not produce a valid workbook")
    scoring_rules = load_scoring_rules(settings.scoring_rules_path)
    summary = run_production_cycle(
        sheet_client,
        scoring_rules,
        mode=args.mode,
        limits=limits,
        job_key=args.job_key,
        stale_after_minutes=args.stale_after_minutes,
        append_run=not args.no_run_log,
    )
    print(json.dumps({"status": "success", **summary.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
