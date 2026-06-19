from __future__ import annotations

import json
from typing import Any

from src.models import utc_now_iso
from src.schema import validate_workbook_or_raise
from src.settings import load_settings
from src.sheets import SheetClient


def _run_timestamp(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")


def _validation_summary(validation_result: Any) -> dict[str, Any]:
    return {
        "ok": validation_result.ok,
        "timezone": validation_result.timezone,
        "expected_timezone": validation_result.expected_timezone,
        "timezone_ok": validation_result.timezone_ok,
        "worksheets_validated": len(validation_result.sheets),
        "worksheet_names": [sheet.worksheet_name for sheet in validation_result.sheets],
    }


def build_sprint16_run_record(validation_summary: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_iso()
    worksheets_validated = int(validation_summary.get("worksheets_validated") or 0)
    return {
        "run_id": f"sprint16_workflow_validation_{_run_timestamp(now)}",
        "run_type": "sprint_16_workflow_validation",
        "source_type": "workflow",
        "source_name": "Daily run schema preflight",
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": worksheets_validated,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "rows_read": worksheets_validated,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(validation_summary, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def run_workflow_validation() -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    validation_result = validate_workbook_or_raise(sheet_client)
    summary = _validation_summary(validation_result)
    sheet_client.append_run(build_sprint16_run_record(summary))
    return {
        "run_mode": "sprint_16_workflow_validation",
        "status": "success",
        "runs_rows_appended": 1,
        **summary,
    }


def main() -> None:
    print(json.dumps(run_workflow_validation(), indent=2))


if __name__ == "__main__":
    main()
