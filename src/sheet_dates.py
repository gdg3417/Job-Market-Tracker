from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from src.models import JobPosting

GOOGLE_SHEETS_EPOCH = date(1899, 12, 30)

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
WEEKLY_VALUE_DATE_FIELDS = ("Week Start", "Week End")


def normalize_sheet_date(value: Any) -> Any:
    """Return an ISO date for values commonly returned from Google Sheets.

    Formatted Google Sheets date cells commonly arrive as m/d/yy strings while
    unformatted reads may return numeric serials. Unknown values are preserved
    so downstream validation can report them without destructive conversion.
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


def normalize_record_dates(
    record: dict[str, Any],
    fields: Iterable[str] = JOB_DATE_FIELDS,
) -> dict[str, Any]:
    normalized = dict(record)
    for field in fields:
        if field in normalized:
            normalized[field] = normalize_sheet_date(normalized[field])
    return normalized


def has_job_identity(record: dict[str, Any]) -> bool:
    return any(
        str(record.get(key, "")).strip()
        for key in ("job_key", "company", "title", "canonical_url")
    )


def normalized_job_from_record(record: dict[str, Any]) -> JobPosting:
    return JobPosting.from_dict(normalize_record_dates(record, JOB_DATE_FIELDS))


def normalize_job(job: JobPosting) -> JobPosting:
    return normalized_job_from_record(job.to_dict())


def normalize_jobs_with_rows(
    records: Iterable[dict[str, Any]],
    *,
    first_data_row: int = 2,
) -> list[tuple[int, JobPosting]]:
    return [
        (index, normalized_job_from_record(record))
        for index, record in enumerate(records, start=first_data_row)
        if has_job_identity(record)
    ]
