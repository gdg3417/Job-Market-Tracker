from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from typing import Any

from src.models import JobPosting
from src.settings import load_settings
from src.sheets import SheetClient
from src.weekly_value import apply_weekly_value

JOB_DATE_FIELDS = (
    "first_seen_date",
    "last_seen_date",
    "closed_date",
    "reviewed_date",
    "application_date",
    "last_application_update",
    "next_action_date",
    "follow_up_date",
)
REJECTED_DATE_FIELDS = ("created_at", "received_date", "updated_at")
GOOGLE_SHEETS_EPOCH = date(1899, 12, 30)


def normalize_sheet_date(value: Any) -> Any:
    """Return an ISO date for values commonly returned from Google Sheets.

    Canonical sheet reads use formatted values, so a real Sheets date cell may
    arrive as ``6/30/26`` even though the underlying cell is numeric. Numeric
    serials are also supported for callers that request unformatted values.
    Unknown values are preserved so existing validation and warning behavior
    remains intact.
    """

    if value in (None, ""):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (GOOGLE_SHEETS_EPOCH + timedelta(days=float(value))).isoformat()
        except (OverflowError, ValueError):
            return value

    text = str(value).strip()
    if not text:
        return value

    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass

    date_token = text.split()[0]
    for pattern in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_token, pattern).date().isoformat()
        except ValueError:
            continue
    return value


def normalize_record_dates(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    normalized = dict(record)
    for field in fields:
        if field in normalized:
            normalized[field] = normalize_sheet_date(normalized[field])
    return normalized


def _read_optional_records(sheet_client: SheetClient, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(worksheet_name)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def _has_job_identity(record: dict[str, Any]) -> bool:
    return any(str(record.get(key, "")).strip() for key in ("job_key", "company", "title", "canonical_url"))


def run_weekly_value_refresh(*, as_of: str | None = None, backfill_weeks: int = 12) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)

    job_records = sheet_client.read_records("Jobs")
    jobs = [
        JobPosting.from_dict(normalize_record_dates(record, JOB_DATE_FIELDS))
        for record in job_records
        if _has_job_identity(record)
    ]
    rejected_rows = [
        normalize_record_dates(record, REJECTED_DATE_FIELDS)
        for record in _read_optional_records(sheet_client, "Rejected_Jobs")
    ]

    result = apply_weekly_value(
        sheet_client,
        as_of=as_of,
        backfill_weeks=backfill_weeks,
        jobs=jobs,
        rejected_job_rows=rejected_rows,
    )
    return {
        "run_mode": "sprint_44_weekly_value_refresh",
        "status": "success",
        "sheet_date_normalization": "enabled",
        **result.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Weekly_Value with Google Sheets date normalization")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--backfill-weeks", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            run_weekly_value_refresh(as_of=args.as_of, backfill_weeks=args.backfill_weeks),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
