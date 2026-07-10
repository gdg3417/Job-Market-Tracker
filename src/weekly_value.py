from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from src.follow_up import evaluate_follow_up
from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

WEEKLY_VALUE_SHEET = "Weekly_Value"
WEEKLY_VALUE_HEADERS = [
    "Week Start",
    "Week End",
    "Jobs Added",
    "Jobs Reviewed",
    "Jobs Dismissed",
    "Jobs Applied",
    "Jobs Moved to Active Status",
    "Jobs Still Not Reviewed Yet",
    "Follow-ups Due",
    "Outstanding Active Roles",
    "Strong Fit Jobs",
    "Stretch Fit Jobs",
    "Auto-Rejected Jobs",
    "Blocked Company Rejects",
    "Too-Senior Rejects or Penalties",
    "Review Completion Rate",
    "Actionable Conversion Rate",
    "Dismissal Rate",
    "Backlog Change",
    "Signal Quality",
    "Noise Removed",
    "Notes",
]

TERMINAL_JOB_STATUSES = {"confirmed_closed", "closed", "expired"}
NOT_REVIEWED_STATUSES = {"", "not reviewed", "not reviewed yet", "not_reviewed"}
DISMISSED_REVIEW_STATUSES = {"dismissed", "rejected", "closed", "withdrawn"}
DISMISSED_INTEREST_DECISIONS = {"dismissed", "not interested", "not_interested"}
ACTIONABLE_REVIEW_STATUSES = {
    "review now",
    "review_now",
    "reviewing",
    "interested",
    "watch",
    "deferred",
    "applied",
    "interviewing",
    "offer",
}
ACTIONABLE_INTEREST_DECISIONS = {"interested", "watch", "deferred", "applied"}
STRONG_ALERT_TIERS = {"immediate review", "immediate_review", "strong fit", "strong_fit"}
TOO_SENIOR_LEVELS = {
    "senior director",
    "vp",
    "vice president",
    "svp",
    "senior vice president",
    "evp",
    "executive vice president",
    "c suite",
    "c-suite",
    "chief executive officer",
    "chief financial officer",
    "chief operating officer",
}


@dataclass(slots=True)
class WeeklyValueResult:
    jobs_read: int
    rejected_rows_read: int
    weeks_written: int
    current_week_start: str
    current_week_end: str
    current_jobs_added: int
    current_jobs_reviewed: int
    current_backlog: int
    current_follow_ups_due: int
    rows_written: int
    generated_at: str
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["warnings"] = values["warnings"] or []
        return values


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _date(value: Any) -> date | None:
    return parse_iso_date(value)


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _week_end(value: date) -> date:
    return _week_start(value) + timedelta(days=6)


def _in_period(value: Any, start: date, end: date) -> bool:
    parsed = _date(value)
    return parsed is not None and start <= parsed <= end


def _first_date(*values: Any) -> date | None:
    parsed = [item for value in values if (item := _date(value)) is not None]
    return min(parsed) if parsed else None


def _last_date(*values: Any) -> date | None:
    parsed = [item for value in values if (item := _date(value)) is not None]
    return max(parsed) if parsed else None


def _review_transition_date(job: JobPosting) -> date | None:
    return _first_date(job.reviewed_date, job.application_date)


def _active_transition_date(job: JobPosting) -> date | None:
    return _last_date(job.last_application_update, job.application_date, job.reviewed_date)


def _job_existed_by(job: JobPosting, end: date) -> bool:
    first_seen = _date(job.first_seen_date)
    return first_seen is not None and first_seen <= end


def _closed_by(job: JobPosting, end: date, as_of: date) -> bool:
    closed_date = _date(job.closed_date)
    if closed_date is not None:
        return closed_date <= end
    return job.status in TERMINAL_JOB_STATUSES and end >= as_of


def _is_dismissed(job: JobPosting) -> bool:
    return _normalize(job.review_status) in DISMISSED_REVIEW_STATUSES or _normalize(job.interest_decision) in DISMISSED_INTEREST_DECISIONS


def _is_actionable(job: JobPosting) -> bool:
    if _normalize(job.review_status) in ACTIONABLE_REVIEW_STATUSES:
        return True
    if _normalize(job.interest_decision) in ACTIONABLE_INTEREST_DECISIONS:
        return True
    return _normalize(job.application_status) in {"applied", "interviewing", "offer"}


def _is_blocked_company_job(job: JobPosting) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return "company_exclusion=true" in explanation or "company_exclusion_reason=blocked_company" in explanation


def _is_too_senior_job(job: JobPosting) -> bool:
    explanation = str(job.score_explanation or "").lower()
    level = _normalize(job.role_level)
    return (
        level in TOO_SENIOR_LEVELS
        or "likely_too_senior" in explanation
        or "seniority_fit=too_senior" in explanation
        or "seniority_fit=excluded" in explanation
        or "seniority_reason=likely_too_senior" in explanation
    )


def _is_stretch_fit(job: JobPosting) -> bool:
    if _is_blocked_company_job(job) or _is_too_senior_job(job):
        return False
    explanation = str(job.score_explanation or "").lower()
    level = _normalize(job.role_level)
    return (
        level == "director"
        or "stretch_seniority_director" in explanation
        or "seniority_fit=stretch" in explanation
    )


def _is_strong_fit(job: JobPosting) -> bool:
    if _is_stretch_fit(job) or _is_blocked_company_job(job) or _is_too_senior_job(job):
        return False
    visible_score = job.verified_total_score if job.verified_total_score is not None else job.total_score
    visible_tier = _normalize(job.verified_alert_tier or job.alert_tier)
    return job.score_status != "excluded" and (visible_score >= 75 or visible_tier in STRONG_ALERT_TIERS)


def _is_auto_rejected_job(job: JobPosting) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return job.score_status == "excluded" or job.alert_tier == "exclude" or "hard_exclude=true" in explanation


def _rejected_row_date(row: dict[str, Any]) -> date | None:
    return _first_date(row.get("created_at"), row.get("received_date"), row.get("updated_at"))


def _is_blocked_company_rejection(row: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in row.values()).lower()
    return "blocked_company" in text or "blocked company" in text or "company_exclusion" in text


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _week_note(*, is_current: bool, missing_job_dates: int, missing_rejected_dates: int, effective_end: date) -> str:
    notes = [
        f"Current week through {effective_end.isoformat()}." if is_current else "Historical metrics reconstructed from durable date fields."
    ]
    if missing_job_dates:
        notes.append(f"{missing_job_dates} Jobs row(s) lacked first_seen_date and were excluded from weekly volume metrics.")
    if missing_rejected_dates:
        notes.append(f"{missing_rejected_dates} rejected row(s) lacked a usable date and were excluded from weekly rejection metrics.")
    notes.append("Historical status metrics are limited where the workbook lacks a complete status transition ledger.")
    return " ".join(notes)


def calculate_week_record(
    jobs: list[JobPosting],
    rejected_job_rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    as_of: date,
) -> dict[str, Any]:
    effective_end = min(end, as_of)
    is_current = start == _week_start(as_of)
    jobs_added = [job for job in jobs if _in_period(job.first_seen_date, start, effective_end)]
    reviewed = [job for job in jobs if (transition := _review_transition_date(job)) is not None and start <= transition <= effective_end]
    dismissed = [job for job in reviewed if _is_dismissed(job)]
    applied = [job for job in jobs if _in_period(job.application_date, start, effective_end)]

    active_jobs = []
    follow_ups_due = []
    for job in jobs:
        if not _job_existed_by(job, effective_end) or _closed_by(job, effective_end, as_of):
            continue
        evaluation = evaluate_follow_up(job, as_of=effective_end.isoformat())
        if evaluation.outstanding_status_flag:
            active_jobs.append(job)
        if evaluation.follow_up_due:
            follow_ups_due.append(job)

    moved_to_active = [
        job
        for job in active_jobs
        if (transition := _active_transition_date(job)) is not None and start <= transition <= effective_end
    ]

    backlog = 0
    for job in jobs:
        if not _job_existed_by(job, effective_end) or _closed_by(job, effective_end, as_of):
            continue
        if _is_auto_rejected_job(job):
            continue
        transition = _review_transition_date(job)
        if transition is None or transition > effective_end:
            if is_current and _normalize(job.review_status) not in NOT_REVIEWED_STATUSES:
                continue
            backlog += 1

    period_rejected_rows = [
        row for row in rejected_job_rows
        if (rejected_date := _rejected_row_date(row)) is not None and start <= rejected_date <= effective_end
    ]
    auto_rejected_jobs = [job for job in jobs_added if _is_auto_rejected_job(job)]
    blocked_jobs = [job for job in jobs_added if _is_blocked_company_job(job)]
    blocked_rejected_rows = [row for row in period_rejected_rows if _is_blocked_company_rejection(row)]
    too_senior = [job for job in jobs_added if _is_too_senior_job(job)]
    strong_fit = [job for job in jobs_added if _is_strong_fit(job)]
    stretch_fit = [job for job in jobs_added if _is_stretch_fit(job)]
    actionable_reviewed = [job for job in reviewed if _is_actionable(job)]

    auto_rejected_count = len(period_rejected_rows) + len(auto_rejected_jobs)
    total_considered = len(jobs_added) + len(period_rejected_rows)
    signal_job_keys = {job.job_key or f"{job.company}|{job.title}|{job.canonical_url}" for job in [*strong_fit, *stretch_fit]}
    missing_job_dates = sum(1 for job in jobs if _date(job.first_seen_date) is None)
    missing_rejected_dates = sum(1 for row in rejected_job_rows if _rejected_row_date(row) is None)

    return {
        "Week Start": start.isoformat(),
        "Week End": end.isoformat(),
        "Jobs Added": len(jobs_added),
        "Jobs Reviewed": len(reviewed),
        "Jobs Dismissed": len(dismissed),
        "Jobs Applied": len(applied),
        "Jobs Moved to Active Status": len(moved_to_active),
        "Jobs Still Not Reviewed Yet": backlog,
        "Follow-ups Due": len(follow_ups_due),
        "Outstanding Active Roles": len(active_jobs),
        "Strong Fit Jobs": len(strong_fit),
        "Stretch Fit Jobs": len(stretch_fit),
        "Auto-Rejected Jobs": auto_rejected_count,
        "Blocked Company Rejects": len(blocked_jobs) + len(blocked_rejected_rows),
        "Too-Senior Rejects or Penalties": len(too_senior),
        "Review Completion Rate": _safe_rate(len(reviewed), len(jobs_added)),
        "Actionable Conversion Rate": _safe_rate(len(actionable_reviewed), len(reviewed)),
        "Dismissal Rate": _safe_rate(len(dismissed), len(reviewed)),
        "Backlog Change": 0,
        "Signal Quality": _safe_rate(len(signal_job_keys), len(jobs_added)),
        "Noise Removed": _safe_rate(auto_rejected_count, total_considered),
        "Notes": _week_note(
            is_current=is_current,
            missing_job_dates=missing_job_dates,
            missing_rejected_dates=missing_rejected_dates,
            effective_end=effective_end,
        ),
    }


def _coerce_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _record_week_start(record: dict[str, Any]) -> date | None:
    return _date(record.get("Week Start") or record.get("week_start"))


def _normalize_existing_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {_normalize(key).replace(" ", "_"): value for key, value in record.items()}
    output: dict[str, Any] = {}
    for header in WEEKLY_VALUE_HEADERS:
        key = _normalize(header).replace(" ", "_")
        output[header] = normalized.get(key, "")
    return output


def build_weekly_records(
    jobs: list[JobPosting],
    rejected_job_rows: list[dict[str, Any]] | None = None,
    *,
    as_of: str | date | None = None,
    backfill_weeks: int = 12,
) -> list[dict[str, Any]]:
    as_of_date = _date(as_of) or _date(today_iso()) or date.today()
    current_start = _week_start(as_of_date)
    weeks = max(1, int(backfill_weeks))
    records = []
    for offset in range(weeks - 1, -1, -1):
        start = current_start - timedelta(days=7 * offset)
        records.append(
            calculate_week_record(
                jobs,
                rejected_job_rows or [],
                start=start,
                end=start + timedelta(days=6),
                as_of=as_of_date,
            )
        )
    return records


def merge_weekly_records(
    existing_records: list[dict[str, Any]],
    generated_records: list[dict[str, Any]],
    *,
    as_of: str | date | None = None,
) -> list[dict[str, Any]]:
    as_of_date = _date(as_of) or _date(today_iso()) or date.today()
    current_start = _week_start(as_of_date)
    previous_start = current_start - timedelta(days=7)

    merged: dict[date, dict[str, Any]] = {}
    for raw_record in existing_records:
        record = _normalize_existing_record(raw_record)
        start = _record_week_start(record)
        if start is not None:
            merged[start] = record

    for record in generated_records:
        start = _record_week_start(record)
        if start is None:
            continue
        if start >= previous_start or start not in merged:
            merged[start] = record

    chronological = [merged[start] for start in sorted(merged)]
    previous_backlog: int | None = None
    for record in chronological:
        backlog = _coerce_int(record.get("Jobs Still Not Reviewed Yet"))
        record["Backlog Change"] = 0 if previous_backlog is None else backlog - previous_backlog
        previous_backlog = backlog
    return list(reversed(chronological))


def build_weekly_values(records: list[dict[str, Any]]) -> list[list[Any]]:
    return [WEEKLY_VALUE_HEADERS, *[[record.get(header, "") for header in WEEKLY_VALUE_HEADERS] for record in records]]


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _worksheet_id(sheet_client: SheetClient, worksheet: Any) -> int:
    worksheet_id = getattr(worksheet, "id", None)
    if worksheet_id is not None:
        return int(worksheet_id)
    metadata = with_quota_backoff(lambda: sheet_client.workbook.fetch_sheet_metadata(), operation_name="fetch workbook metadata")
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties") or {}
        if properties.get("title") == WEEKLY_VALUE_SHEET:
            return int(properties["sheetId"])
    raise ValueError(f"Could not resolve worksheet id for {WEEKLY_VALUE_SHEET}")


def _formatting_requests(sheet_id: int, row_count: int) -> list[dict[str, Any]]:
    column_count = len(WEEKLY_VALUE_HEADERS)
    return [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}},
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": max(1, row_count),
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    }
                }
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": column_count},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.72, "green": 0.72, "blue": 0.72},
                        "textFormat": {"bold": True},
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max(2, row_count), "startColumnIndex": 15, "endColumnIndex": 18},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": max(2, row_count), "startColumnIndex": 19, "endColumnIndex": 21},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": column_count}
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 21, "endIndex": 22},
                "properties": {"pixelSize": 420},
                "fields": "pixelSize",
            }
        },
    ]


def _read_existing_records(sheet_client: SheetClient) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(WEEKLY_VALUE_SHEET)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def write_weekly_value(sheet_client: SheetClient, values: list[list[Any]]) -> Any:
    worksheet = sheet_client.ensure_worksheet(
        WEEKLY_VALUE_SHEET,
        rows=max(1000, len(values) + 10),
        cols=len(WEEKLY_VALUE_HEADERS),
    )
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {WEEKLY_VALUE_SHEET}")
    end_cell = f"{_column_name(len(WEEKLY_VALUE_HEADERS))}{len(values)}"
    with_quota_backoff(
        lambda: worksheet.update(range_name=f"A1:{end_cell}", values=values, value_input_option="USER_ENTERED"),
        operation_name=f"write worksheet {WEEKLY_VALUE_SHEET}",
    )
    return worksheet


def apply_weekly_value(
    sheet_client: SheetClient,
    *,
    as_of: str | date | None = None,
    backfill_weeks: int = 12,
    jobs: list[JobPosting] | None = None,
    rejected_job_rows: list[dict[str, Any]] | None = None,
) -> WeeklyValueResult:
    as_of_date = _date(as_of) or _date(today_iso()) or date.today()
    jobs = jobs if jobs is not None else [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    rejected_job_rows = rejected_job_rows if rejected_job_rows is not None else sheet_client.read_records("Rejected_Jobs")
    existing_records = _read_existing_records(sheet_client)
    generated = build_weekly_records(jobs, rejected_job_rows, as_of=as_of_date, backfill_weeks=backfill_weeks)
    merged = merge_weekly_records(existing_records, generated, as_of=as_of_date)
    values = build_weekly_values(merged)
    worksheet = write_weekly_value(sheet_client, values)

    warnings: list[str] = []
    try:
        requests = _formatting_requests(_worksheet_id(sheet_client, worksheet), len(values))
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name=f"format worksheet {WEEKLY_VALUE_SHEET}",
        )
    except Exception as exc:
        warnings.append(f"Weekly_Value formatting was not applied: {exc}")

    current_start = _week_start(as_of_date)
    current_record = next((record for record in merged if _record_week_start(record) == current_start), {})
    return WeeklyValueResult(
        jobs_read=len(jobs),
        rejected_rows_read=len(rejected_job_rows),
        weeks_written=len(merged),
        current_week_start=current_start.isoformat(),
        current_week_end=_week_end(as_of_date).isoformat(),
        current_jobs_added=_coerce_int(current_record.get("Jobs Added")),
        current_jobs_reviewed=_coerce_int(current_record.get("Jobs Reviewed")),
        current_backlog=_coerce_int(current_record.get("Jobs Still Not Reviewed Yet")),
        current_follow_ups_due=_coerce_int(current_record.get("Follow-ups Due")),
        rows_written=len(values),
        generated_at=utc_now_iso(),
        warnings=warnings,
    )


def run_weekly_value_refresh(*, as_of: str | None = None, backfill_weeks: int = 12) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_weekly_value(sheet_client, as_of=as_of, backfill_weeks=backfill_weeks)
    return {"run_mode": "sprint_44_weekly_value_refresh", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the Job Market Tracker Weekly_Value tab")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--backfill-weeks", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_weekly_value_refresh(as_of=args.as_of, backfill_weeks=args.backfill_weeks), indent=2))


if __name__ == "__main__":
    main()
