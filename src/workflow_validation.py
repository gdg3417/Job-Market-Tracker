from __future__ import annotations

import json
import os
import re
from pathlib import Path
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


def _cached_validation_summary() -> dict[str, Any] | None:
    """Reuse the schema result produced by the immediately preceding workflow step."""
    runner_temp = os.environ.get("RUNNER_TEMP", "").strip()
    if not runner_temp:
        return None

    path = Path(runner_temp) / "schema_validation.json"
    if not path.exists():
        return None

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None

    match = re.search(r"\{.*\}\s*$", text, re.S)
    data = json.loads(match.group(0) if match else text)
    if not data.get("ok"):
        raise ValueError("Cached workbook schema validation did not pass")

    sheets = data.get("sheets") or []
    return {
        "ok": True,
        "timezone": str(data.get("timezone") or ""),
        "expected_timezone": str(data.get("expected_timezone") or ""),
        "timezone_ok": bool(data.get("timezone_ok")),
        "worksheets_validated": len(sheets),
        "worksheet_names": [str(sheet.get("worksheet_name") or "") for sheet in sheets],
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
    summary = _cached_validation_summary()
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    if summary is None:
        validation_result = validate_workbook_or_raise(sheet_client)
        summary = _validation_summary(validation_result)
    sheet_client.append_run(build_sprint16_run_record(summary))
    return {
        "run_mode": "sprint_16_workflow_validation",
        "status": "success",
        "validation_source": "cached" if os.environ.get("RUNNER_TEMP") else "live",
        "runs_rows_appended": 1,
        **summary,
    }


def main() -> None:
    print(json.dumps(run_workflow_validation(), indent=2))


if __name__ == "__main__":
    main()
