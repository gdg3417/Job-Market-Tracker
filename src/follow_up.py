from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

FOLLOW_UP_QUEUE_SHEET = "Follow_Up_Queue"

FOLLOW_UP_QUEUE_HEADERS = [
    "job_key",
    "company",
    "title",
    "outstanding_status",
    "last_status_update_date",
    "days_since_status_update",
    "follow_up_due",
    "follow_up_reason",
    "next_action",
    "next_action_date",
    "follow_up_date",
    "application_status",
    "review_status",
    "interview_stage",
    "review_notes",
    "canonical_url",
]

TERMINAL_STATUSES = {
    "dismissed",
    "rejected",
    "closed",
    "withdrawn",
    "not reviewed yet",
    "not reviewed",
    "not_reviewed",
    "not started",
    "not_started",
    "drafting",
}

STATUS_THRESHOLDS_DAYS = {
    "applied": 7,
    "in review": 7,
    "recruiter screen": 4,
    "hiring manager screen": 4,
    "interviewing": 4,
    "take home case": 4,
    "waiting on response": 6,
    "offer negotiation": 2,
}

STATUS_LABELS = {
    "applied": "Applied",
    "in review": "In Review",
    "recruiter screen": "Recruiter Screen",
    "hiring manager screen": "Hiring Manager Screen",
    "interviewing": "Interviewing",
    "take home case": "Take-home / Case",
    "waiting on response": "Waiting on Response",
    "offer negotiation": "Offer / Negotiation",
}


@dataclass(frozen=True, slots=True)
class FollowUpEvaluation:
    outstanding_status: str = ""
    last_status_update_date: str = ""
    days_since_status_update: int | None = None
    follow_up_due: bool = False
    follow_up_reason: str = ""
    outstanding_status_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FollowUpQueueResult:
    jobs_read: int
    outstanding_rows: int
    follow_up_due_rows: int
    rows_written: int
    generated_at: str
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["warnings"] = values["warnings"] or []
        return values


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _classify_interview_stage(value: Any) -> str:
    stage = _normalize(value)
    if not stage:
        return ""
    if "recruiter" in stage or "phone screen" in stage or "talent acquisition" in stage:
        return "recruiter screen"
    if "hiring manager" in stage or "manager screen" in stage:
        return "hiring manager screen"
    if "take home" in stage or "case study" in stage or stage == "case" or "assessment" in stage:
        return "take home case"
    if "offer" in stage or "negotiat" in stage:
        return "offer negotiation"
    if "waiting" in stage or "response" in stage or "feedback" in stage:
        return "waiting on response"
    if "interview" in stage or "panel" in stage or "onsite" in stage or "final round" in stage:
        return "interviewing"
    return ""


def infer_outstanding_status(job: JobPosting) -> str:
    application_status = _normalize(job.application_status)
    review_status = _normalize(job.review_status)
    interview_stage = _classify_interview_stage(job.interview_stage)

    if application_status in TERMINAL_STATUSES or review_status in TERMINAL_STATUSES:
        return ""
    if interview_stage:
        return interview_stage
    if application_status == "offer" or review_status == "offer":
        return "offer negotiation"
    if application_status == "interviewing" or review_status == "interviewing":
        return "interviewing"
    if application_status == "applied" or review_status == "applied":
        return "applied"
    if review_status in {"reviewing", "in review", "review now", "review_now"}:
        return "in review"

    combined = _normalize(" ".join([job.next_action, job.interview_stage]))
    if "waiting" in combined or "follow up" in combined or "response" in combined:
        return "waiting on response"
    return ""


def _most_recent_date(values: list[Any]) -> date | None:
    parsed = [parsed_value for value in values if (parsed_value := parse_iso_date(value)) is not None]
    return max(parsed) if parsed else None


def status_update_date(job: JobPosting, status: str) -> date | None:
    if status == "applied":
        candidates = [job.last_application_update, job.application_date, job.reviewed_date]
    elif status == "in review":
        candidates = [job.reviewed_date, job.last_application_update, job.application_date]
    else:
        candidates = [job.last_application_update, job.reviewed_date, job.application_date]
    return _most_recent_date(candidates)


def evaluate_follow_up(job: JobPosting, *, as_of: str | date | None = None) -> FollowUpEvaluation:
    status = infer_outstanding_status(job)
    if not status:
        return FollowUpEvaluation()

    as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso()) or date.today()
    last_update = status_update_date(job, status)
    explicit_due_date = _most_recent_date([job.next_action_date, job.follow_up_date])

    if last_update is None:
        return FollowUpEvaluation(
            outstanding_status=STATUS_LABELS[status],
            follow_up_due=True,
            follow_up_reason=f"{STATUS_LABELS[status]} is missing a status update date.",
            outstanding_status_flag=True,
        )

    days_since = max(0, (as_of_date - last_update).days)
    threshold = STATUS_THRESHOLDS_DAYS[status]
    threshold_due = days_since >= threshold
    explicit_due = explicit_due_date is not None and explicit_due_date <= as_of_date
    due = threshold_due or explicit_due

    if explicit_due:
        reason = f"Scheduled follow-up date {explicit_due_date.isoformat()} is due for {STATUS_LABELS[status]}."
    elif threshold_due:
        reason = f"{STATUS_LABELS[status]} has not been updated for {days_since} calendar days (threshold: {threshold})."
    else:
        reason = f"{STATUS_LABELS[status]} was updated {days_since} calendar days ago; follow-up is not due yet."

    return FollowUpEvaluation(
        outstanding_status=STATUS_LABELS[status],
        last_status_update_date=last_update.isoformat(),
        days_since_status_update=days_since,
        follow_up_due=due,
        follow_up_reason=reason,
        outstanding_status_flag=True,
    )


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    return value


def job_to_follow_up_row(job: JobPosting, *, as_of: str | date | None = None) -> list[Any] | None:
    evaluation = evaluate_follow_up(job, as_of=as_of)
    if not evaluation.outstanding_status_flag:
        return None
    values = job.to_dict() | evaluation.to_dict()
    return [_cell(values.get(header, "")) for header in FOLLOW_UP_QUEUE_HEADERS]


def build_follow_up_rows(jobs: list[JobPosting], *, as_of: str | date | None = None) -> list[list[Any]]:
    rows = [row for job in jobs if (row := job_to_follow_up_row(job, as_of=as_of)) is not None]
    due_index = FOLLOW_UP_QUEUE_HEADERS.index("follow_up_due")
    days_index = FOLLOW_UP_QUEUE_HEADERS.index("days_since_status_update")
    company_index = FOLLOW_UP_QUEUE_HEADERS.index("company")
    title_index = FOLLOW_UP_QUEUE_HEADERS.index("title")
    return sorted(
        rows,
        key=lambda row: (
            0 if row[due_index] is True else 1,
            -(int(row[days_index]) if row[days_index] not in {"", None} else 10_000),
            str(row[company_index]).lower(),
            str(row[title_index]).lower(),
        ),
    )


def build_follow_up_values(jobs: list[JobPosting], *, as_of: str | date | None = None) -> list[list[Any]]:
    return [FOLLOW_UP_QUEUE_HEADERS, *build_follow_up_rows(jobs, as_of=as_of)]


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
        if properties.get("title") == FOLLOW_UP_QUEUE_SHEET:
            return int(properties["sheetId"])
    raise ValueError(f"Could not resolve worksheet id for {FOLLOW_UP_QUEUE_SHEET}")


def _formatting_requests(sheet_id: int, row_count: int) -> list[dict[str, Any]]:
    column_count = len(FOLLOW_UP_QUEUE_HEADERS)
    return [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 3}},
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
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
            }
        },
    ]


def write_follow_up_queue(sheet_client: SheetClient, values: list[list[Any]]) -> Any:
    worksheet = sheet_client.ensure_worksheet(
        FOLLOW_UP_QUEUE_SHEET,
        rows=max(1000, len(values) + 10),
        cols=len(FOLLOW_UP_QUEUE_HEADERS),
    )
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {FOLLOW_UP_QUEUE_SHEET}")
    end_cell = f"{_column_name(len(FOLLOW_UP_QUEUE_HEADERS))}{len(values)}"
    with_quota_backoff(
        lambda: worksheet.update(range_name=f"A1:{end_cell}", values=values, value_input_option="USER_ENTERED"),
        operation_name=f"write worksheet {FOLLOW_UP_QUEUE_SHEET}",
    )
    return worksheet


def apply_follow_up_queue(sheet_client: SheetClient, *, as_of: str | date | None = None) -> FollowUpQueueResult:
    jobs = [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    values = build_follow_up_values(jobs, as_of=as_of)
    worksheet = write_follow_up_queue(sheet_client, values)
    warnings: list[str] = []
    try:
        requests = _formatting_requests(_worksheet_id(sheet_client, worksheet), len(values))
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name=f"format worksheet {FOLLOW_UP_QUEUE_SHEET}",
        )
    except Exception as exc:  # pragma: no cover - live Sheets only
        warnings.append(f"Follow_Up_Queue formatting was not applied: {exc}")

    due_index = FOLLOW_UP_QUEUE_HEADERS.index("follow_up_due")
    rows = values[1:]
    return FollowUpQueueResult(
        jobs_read=len(jobs),
        outstanding_rows=len(rows),
        follow_up_due_rows=sum(1 for row in rows if row[due_index] is True),
        rows_written=len(values),
        generated_at=utc_now_iso(),
        warnings=warnings,
    )


def run_follow_up_refresh(*, as_of: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_follow_up_queue(sheet_client, as_of=as_of)
    return {"run_mode": "sprint_43_follow_up_refresh", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the Job Market Tracker Follow_Up_Queue tab")
    parser.add_argument("--refresh", action="store_true", help="Refresh Follow_Up_Queue from canonical Jobs data")
    parser.add_argument("--as-of", default=None, help="Optional YYYY-MM-DD date used for deterministic validation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_follow_up_refresh(as_of=args.as_of), indent=2))


if __name__ == "__main__":
    main()
