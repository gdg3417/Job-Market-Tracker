from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.verification_health_blockers import classify_blocker
from src.verification_health_dashboard import build_dashboard_section, write_dashboard_section
from src.verification_health_metrics import calculate_verification_health
from src.verification_health_models import (
    BLOCKER_REASONS,
    AgingMetric,
    Blocker,
    FunnelMetric,
    HealthComponent,
    HealthThresholds,
    VerificationHealthResult,
    parse_datetime,
    utc_now,
)


def build_run_record(result: VerificationHealthResult, *, dashboard_rows_written: int = 0) -> dict[str, Any]:
    generated = parse_datetime(result.generated_at) or utc_now()
    failed = 1 if result.overall_classification == "Blocked" else 0
    notes = result.compact_dict() | {"dashboard_rows_written": dashboard_rows_written}
    return {
        "run_id": result.run_id,
        "run_type": "sprint_33_verification_health",
        "source_type": "jobs",
        "source_name": "Verification observability",
        "status": "blocked" if failed else "success",
        "started_at": result.generated_at,
        "finished_at": result.generated_at,
        "duration_seconds": 0,
        "records_found": result.records_read.get("jobs", 0),
        "records_inserted": 0,
        "records_updated": dashboard_rows_written,
        "records_failed": failed,
        "rows_read": sum(result.records_read.values()),
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "; ".join(result.critical_overrides),
        "notes": json.dumps(notes, sort_keys=True, separators=(",", ":")),
        "created_at": generated.isoformat().replace("+00:00", "Z"),
        "updated_at": generated.isoformat().replace("+00:00", "Z"),
    }


def upsert_run_record(sheet_client: Any, record: dict[str, Any]) -> str:
    rows = sheet_client.read_records_with_row_numbers("Runs")
    for row_number, existing in rows:
        if str(existing.get("run_id") or "") == str(record.get("run_id") or ""):
            sheet_client.update_record("Runs", row_number, record)
            return "updated"
    sheet_client.append_run(record)
    return "inserted"


def _read_optional(sheet_client: Any, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return list(sheet_client.read_records(worksheet_name))
    except Exception as exc:
        if exc.__class__.__name__ in {"WorksheetNotFound", "KeyError"}:
            return []
        raise


def calculate_from_workbook(
    sheet_client: Any,
    *,
    thresholds: HealthThresholds | None = None,
    as_of: datetime | None = None,
    run_id: str = "",
) -> VerificationHealthResult:
    return calculate_verification_health(
        jobs=_read_optional(sheet_client, "Jobs"),
        job_sources=_read_optional(sheet_client, "Job_Sources"),
        queue_rows=_read_optional(sheet_client, "Enrichment_Queue"),
        evidence_rows=_read_optional(sheet_client, "Enrichment_Evidence"),
        runs_rows=_read_optional(sheet_client, "Runs"),
        target_company_rows=_read_optional(sheet_client, "Target_Companies"),
        config_company_rows=_read_optional(sheet_client, "Config_Companies"),
        thresholds=thresholds,
        as_of=as_of,
        run_id=run_id,
    )


def load_thresholds(path: str | Path) -> HealthThresholds:
    return HealthThresholds.from_yaml(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate and persist Sprint 33 verification health")
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--run", action="store_true")
    execution.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default="config/verification_health.yml")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--no-run-log", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise
    from src.settings import load_settings
    from src.sheets import SheetClient

    client = SheetClient.from_settings(load_settings())
    thresholds = load_thresholds(args.config)
    as_of = parse_datetime(args.as_of) if args.as_of else None
    if args.dry_run:
        validate_workbook_or_raise(client)
    else:
        migration = migrate_trailing_headers(client)
        if not migration.ok:
            raise RuntimeError("Workbook schema migration did not produce a valid workbook")

    result = calculate_from_workbook(client, thresholds=thresholds, as_of=as_of, run_id=args.run_id)
    dashboard_rows = 0
    history_action = "not_written"
    if args.run:
        if not args.no_dashboard:
            dashboard_rows = write_dashboard_section(client, result)
        if not args.no_run_log:
            history_action = upsert_run_record(client, build_run_record(result, dashboard_rows_written=dashboard_rows))
    print(json.dumps(
        result.compact_dict() | {
            "dashboard_rows_written": dashboard_rows,
            "history_action": history_action,
            "dry_run": args.dry_run,
        },
        indent=2,
        sort_keys=True,
    ))


__all__ = [
    "BLOCKER_REASONS", "AgingMetric", "Blocker", "FunnelMetric", "HealthComponent",
    "HealthThresholds", "VerificationHealthResult", "build_dashboard_section",
    "build_run_record", "calculate_from_workbook", "calculate_verification_health",
    "classify_blocker", "load_thresholds", "upsert_run_record", "write_dashboard_section",
]


if __name__ == "__main__":
    main()
