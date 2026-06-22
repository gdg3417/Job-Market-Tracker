from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.models import utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient

CENTRAL_TIMEZONE = ZoneInfo("America/Chicago")
DAILY_COMPLETION_RUN_TYPE = "daily_workflow_completion"


def central_date(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CENTRAL_TIMEZONE).date()


def _record_central_date(record: dict[str, Any]) -> date | None:
    for key in ("finished_at", "started_at", "created_at"):
        value = str(record.get(key) or "").strip()
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(CENTRAL_TIMEZONE).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def has_successful_daily_completion(
    records: Iterable[dict[str, Any]],
    target_date: date,
) -> bool:
    return any(
        str(record.get("run_type") or "").strip() == DAILY_COMPLETION_RUN_TYPE
        and str(record.get("status") or "").strip().lower() == "success"
        and _record_central_date(record) == target_date
        for record in records
    )


def daily_run_gate_decision(
    *,
    event_name: str,
    run_records: Iterable[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    target_date = central_date(now)
    manual_dispatch = event_name != "schedule"
    completed = has_successful_daily_completion(run_records, target_date)
    should_run = manual_dispatch or not completed
    if manual_dispatch:
        result = "manual_dispatch_allowed"
    elif completed:
        result = "skipped_already_completed"
    else:
        result = "scheduled_run_required"
    return {
        "should_run": should_run,
        "gate_result": result,
        "central_date": target_date.isoformat(),
        "successful_completion_exists": completed,
    }


def build_daily_completion_record(*, gate_result: str = "workflow_completed") -> dict[str, Any]:
    now = utc_now_iso()
    timestamp = now.replace(":", "").replace("-", "").replace("+00:00", "Z")
    return {
        "run_id": f"daily_workflow_completion_{timestamp}",
        "run_type": DAILY_COMPLETION_RUN_TYPE,
        "source_type": "workflow",
        "source_name": "GitHub Actions daily run",
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": 0,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "rows_read": 0,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(
            {
                "central_date": central_date().isoformat(),
                "gate_result": gate_result,
            },
            sort_keys=True,
        ),
        "created_at": now,
        "updated_at": now,
    }


def check_daily_run_gate(*, event_name: str) -> dict[str, Any]:
    sheet_client = SheetClient.from_settings(load_settings())
    run_records = sheet_client.read_records("Runs")
    return daily_run_gate_decision(
        event_name=event_name,
        run_records=run_records,
    )


def mark_daily_run_success(*, gate_result: str = "workflow_completed") -> dict[str, Any]:
    sheet_client = SheetClient.from_settings(load_settings())
    record = build_daily_completion_record(gate_result=gate_result)
    sheet_client.append_run(record)
    return {
        "status": "success",
        "run_id": record["run_id"],
        "central_date": central_date().isoformat(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate the daily workflow by Central calendar date")
    parser.add_argument("--check", action="store_true", help="Check whether the daily workflow should run")
    parser.add_argument("--mark-success", action="store_true", help="Append the successful daily completion record")
    parser.add_argument(
        "--event-name",
        default=os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch"),
        help="GitHub event name, usually schedule or workflow_dispatch",
    )
    parser.add_argument(
        "--gate-result",
        default="workflow_completed",
        help="Gate result to include in the completion record",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mark_success:
        result = mark_daily_run_success(gate_result=args.gate_result)
    else:
        result = check_daily_run_gate(event_name=args.event_name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
