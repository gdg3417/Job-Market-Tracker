from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from src.models import JobPosting, utc_now_iso
from src.seniority import evaluate_seniority_fit
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

REVIEW_QUEUE_SHEET = "Review_Queue"

REVIEW_QUEUE_HEADERS = [
    "job_key",
    "company",
    "title",
    "role_level",
    "seniority_fit",
    "seniority_reason",
    "location",
    "canonical_url",
    "potential_priority",
    "potential_priority_score",
    "score_status",
    "evidence_completeness_score",
    "enrichment_status",
    "enrichment_match_confidence",
    "manual_authoritative_url",
    "move_value_classification",
    "move_value_notes",
    "work_model",
    "base_salary_min",
    "base_salary_max",
    "compensation_source_type",
    "commute_bucket",
    "review_status",
    "reviewed_date",
    "interest_decision",
    "manual_priority",
    "manual_fit_rating",
    "review_notes",
    "next_action",
    "next_action_date",
    "follow_up_date",
    "application_status",
    "application_date",
    "resume_version",
    "referral_or_contact",
    "source_primary",
    "source_job_id",
]

ENRICHMENT_PROBLEM_STATUSES = {"not_found", "ambiguous", "retryable_failure", "permanent_failure"}
REVIEW_ACTION_STATUSES = {"review_now", "reviewing", "interested", "watch", "deferred", "applied", "interviewing", "offer"}
TERMINAL_REVIEW_STATUSES = {"dismissed", "rejected", "withdrawn", "closed"}
TERMINAL_JOB_STATUSES = {"confirmed_closed", "closed", "expired"}
TOO_SENIOR_QUEUE_FITS = {"too_senior", "excluded"}

REVIEW_STATUS_SORT_ORDER = {
    "review_now": 0,
    "not_reviewed": 1,
    "reviewing": 2,
    "interested": 3,
    "watch": 4,
    "deferred": 5,
    "applied": 6,
    "interviewing": 7,
    "offer": 8,
    "dismissed": 9,
    "rejected": 10,
    "withdrawn": 11,
    "closed": 12,
}

DATE_HEADERS = {"reviewed_date", "next_action_date", "follow_up_date", "application_date"}
WRAPPED_HEADERS = {"review_notes", "move_value_notes"}
FILTERABLE_HEADERS = {
    "role_level",
    "seniority_fit",
    "seniority_reason",
    "review_status",
    "interest_decision",
    "manual_priority",
    "potential_priority",
    "score_status",
    "enrichment_status",
    "move_value_classification",
    "application_status",
    "next_action_date",
    "company",
    "title",
    "location",
    "work_model",
    "compensation_source_type",
    "commute_bucket",
}


@dataclass(slots=True)
class ReviewQueueResult:
    jobs_read: int
    review_queue_rows: int
    review_queue_rows_written: int
    generated_at: str
    manual_sync_mode: str = "read_only"
    jobs_filter_applied: bool = False
    review_queue_filter_applied: bool = False
    review_queue_freeze_applied: bool = False
    jobs_freeze_applied: bool = False
    filter_views_created: int = 0
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["warnings"] = values["warnings"] or []
        return values


def _has_identity(job: JobPosting) -> bool:
    return any(str(value or "").strip() for value in [job.job_key, job.company, job.title, job.canonical_url])


def _has_manual_review_state(job: JobPosting) -> bool:
    return any(
        str(value or "").strip()
        for value in [
            job.review_status if job.review_status != "not_reviewed" else "",
            job.interest_decision,
            job.manual_priority if job.manual_priority is not None else "",
            job.manual_fit_rating if job.manual_fit_rating is not None else "",
            job.review_notes,
            job.manual_authoritative_url,
            job.dismissal_reason,
            job.dismissal_detail,
            job.application_status,
            job.application_date,
            job.resume_version,
            job.referral_or_contact,
            job.next_action,
            job.next_action_date,
            job.follow_up_date,
        ]
    )


def _score_tag(job: JobPosting, tag_name: str) -> str:
    pattern = re.compile(rf"(?:^|;)\s*{re.escape(tag_name)}=([^;]+)")
    match = pattern.search(str(job.score_explanation or ""))
    return match.group(1).strip() if match else ""


def seniority_review_fields(job: JobPosting) -> dict[str, str]:
    evaluated = evaluate_seniority_fit(job.title, job.role_level)
    return {
        "role_level": str(job.role_level or evaluated.normalized_level or ""),
        "seniority_fit": _score_tag(job, "seniority_fit") or evaluated.seniority_fit,
        "seniority_reason": _score_tag(job, "seniority_reason") or evaluated.reason_code,
    }


def _is_too_senior_for_viable_queue(job: JobPosting) -> bool:
    return seniority_review_fields(job)["seniority_fit"] in TOO_SENIOR_QUEUE_FITS


def should_include_review_queue_job(job: JobPosting) -> bool:
    """Return True when a Jobs row belongs on the operational review surface."""

    if not _has_identity(job):
        return False
    if _has_manual_review_state(job):
        return True
    if job.status in TERMINAL_JOB_STATUSES:
        return False
    if job.score_status == "excluded" and job.potential_priority == "excluded":
        return False
    if _is_too_senior_for_viable_queue(job):
        return False
    if job.review_status in REVIEW_ACTION_STATUSES or job.review_status in TERMINAL_REVIEW_STATUSES:
        return True
    if job.application_status in {"applied", "interviewing", "offer"}:
        return True
    if job.potential_priority in {"high", "medium"}:
        return True
    if job.score_status in {"verified", "partially_verified"}:
        return True
    if job.enrichment_status in ENRICHMENT_PROBLEM_STATUSES or job.enrichment_status in {"pending", "in_progress", "partial"}:
        return True
    return job.total_score >= 50


def _manual_priority_sort(job: JobPosting) -> int:
    return -(job.manual_priority if job.manual_priority is not None else -1)


def _review_status_sort(job: JobPosting) -> int:
    return REVIEW_STATUS_SORT_ORDER.get(job.review_status or "not_reviewed", 99)


def _date_sort_value(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if text else "9999-12-31"


def review_queue_sort_key(job: JobPosting) -> tuple[Any, ...]:
    return (
        _manual_priority_sort(job),
        _review_status_sort(job),
        -int(job.potential_priority_score or 0),
        -int(job.evidence_completeness_score or 0),
        _date_sort_value(job.next_action_date),
        str(job.company or "").lower(),
        str(job.title or "").lower(),
    )


def sort_review_queue_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    return sorted(jobs, key=review_queue_sort_key)


def _cell(value: Any) -> Any:
    return "" if value is None else value


def job_to_review_queue_row(job: JobPosting) -> list[Any]:
    values = job.to_dict() | seniority_review_fields(job)
    return [_cell(values.get(header, "")) for header in REVIEW_QUEUE_HEADERS]


def build_review_queue_rows(jobs: list[JobPosting]) -> list[list[Any]]:
    selected_jobs = [job for job in jobs if should_include_review_queue_job(job)]
    return [job_to_review_queue_row(job) for job in sort_review_queue_jobs(selected_jobs)]


def build_review_queue_values(jobs: list[JobPosting]) -> list[list[Any]]:
    return [REVIEW_QUEUE_HEADERS, *build_review_queue_rows(jobs)]


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _worksheet_id(sheet_client: SheetClient, worksheet: Any, worksheet_name: str) -> int:
    worksheet_id = getattr(worksheet, "id", None)
    if worksheet_id is not None:
        return int(worksheet_id)
    metadata = with_quota_backoff(lambda: sheet_client.workbook.fetch_sheet_metadata(), operation_name="fetch workbook metadata")
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties") or {}
        if properties.get("title") == worksheet_name:
            return int(properties["sheetId"])
    raise ValueError(f"Could not resolve worksheet id for {worksheet_name}")


def _filter_range(sheet_id: int, row_count: int, column_count: int) -> dict[str, int]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": 0,
        "endRowIndex": max(1, row_count),
        "startColumnIndex": 0,
        "endColumnIndex": max(1, column_count),
    }


def _dimension_width_request(sheet_id: int, start_index: int, end_index: int, pixel_size: int) -> dict[str, Any]:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start_index, "endIndex": end_index},
            "properties": {"pixelSize": pixel_size},
            "fields": "pixelSize",
        }
    }


def _repeat_cell_request(sheet_id: int, start_column: int, end_column: int, cell_format: dict[str, Any]) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": start_column, "endColumnIndex": end_column},
            "cell": {"userEnteredFormat": cell_format},
            "fields": ",".join(f"userEnteredFormat.{key}" for key in cell_format),
        }
    }


def _base_filter_and_freeze_requests(
    *,
    sheet_id: int,
    row_count: int,
    column_count: int,
    frozen_row_count: int,
    frozen_column_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": frozen_row_count, "frozenColumnCount": frozen_column_count},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        {"setBasicFilter": {"filter": {"range": _filter_range(sheet_id, row_count, column_count)}}},
    ]


def _review_queue_formatting_requests(sheet_id: int, row_count: int) -> list[dict[str, Any]]:
    requests = _base_filter_and_freeze_requests(
        sheet_id=sheet_id,
        row_count=row_count,
        column_count=len(REVIEW_QUEUE_HEADERS),
        frozen_row_count=1,
        frozen_column_count=7,
    )
    requests.append(
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(REVIEW_QUEUE_HEADERS)},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
            }
        }
    )
    for header in WRAPPED_HEADERS:
        index = REVIEW_QUEUE_HEADERS.index(header)
        requests.append(_repeat_cell_request(sheet_id, index, index + 1, {"wrapStrategy": "WRAP"}))
    for header in DATE_HEADERS.intersection(REVIEW_QUEUE_HEADERS):
        index = REVIEW_QUEUE_HEADERS.index(header)
        requests.append(_repeat_cell_request(sheet_id, index, index + 1, {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}))
    widths = {
        "job_key": 190,
        "company": 160,
        "title": 280,
        "seniority_reason": 230,
        "location": 180,
        "canonical_url": 120,
        "manual_authoritative_url": 120,
        "move_value_notes": 260,
        "review_notes": 300,
        "next_action": 220,
        "referral_or_contact": 180,
    }
    for header, width in widths.items():
        index = REVIEW_QUEUE_HEADERS.index(header)
        requests.append(_dimension_width_request(sheet_id, index, index + 1, width))
    return requests


def _jobs_filter_and_freeze_requests(sheet_id: int, row_count: int, column_count: int) -> list[dict[str, Any]]:
    return _base_filter_and_freeze_requests(
        sheet_id=sheet_id,
        row_count=row_count,
        column_count=column_count,
        frozen_row_count=1,
        frozen_column_count=4,
    )


def _apply_batch_requests(sheet_client: SheetClient, requests: list[dict[str, Any]], operation_name: str) -> None:
    if requests:
        with_quota_backoff(lambda: sheet_client.workbook.batch_update({"requests": requests}), operation_name=operation_name)


def write_review_queue_values(sheet_client: SheetClient, values: list[list[Any]]) -> Any:
    worksheet = sheet_client.ensure_worksheet(REVIEW_QUEUE_SHEET, rows=max(1000, len(values) + 10), cols=len(REVIEW_QUEUE_HEADERS))
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {REVIEW_QUEUE_SHEET}")
    if values:
        end_cell = f"{_column_name(len(REVIEW_QUEUE_HEADERS))}{len(values)}"
        with_quota_backoff(
            lambda: worksheet.update(range_name=f"A1:{end_cell}", values=values, value_input_option="USER_ENTERED"),
            operation_name=f"write worksheet {REVIEW_QUEUE_SHEET}",
        )
    return worksheet


def apply_review_queue(sheet_client: SheetClient) -> ReviewQueueResult:
    jobs_with_rows = sheet_client.read_jobs_with_row_numbers()
    jobs = [job for _, job in jobs_with_rows]
    values = build_review_queue_values(jobs)
    worksheet = write_review_queue_values(sheet_client, values)
    warnings: list[str] = []
    review_filter_applied = False
    review_freeze_applied = False
    jobs_filter_applied = False
    jobs_freeze_applied = False
    try:
        review_sheet_id = _worksheet_id(sheet_client, worksheet, REVIEW_QUEUE_SHEET)
        _apply_batch_requests(sheet_client, _review_queue_formatting_requests(review_sheet_id, len(values)), "format Review_Queue worksheet")
        review_filter_applied = True
        review_freeze_applied = True
    except Exception as exc:  # pragma: no cover, exercised by live Sheets only
        warnings.append(f"Review_Queue formatting was not applied: {exc}")
    try:
        jobs_worksheet = sheet_client.get_worksheet("Jobs")
        jobs_sheet_id = _worksheet_id(sheet_client, jobs_worksheet, "Jobs")
        jobs_headers = sheet_client.worksheet_headers("Jobs")
        _apply_batch_requests(sheet_client, _jobs_filter_and_freeze_requests(jobs_sheet_id, len(jobs_with_rows) + 1, len(jobs_headers)), "format Jobs worksheet")
        jobs_filter_applied = True
        jobs_freeze_applied = True
    except Exception as exc:  # pragma: no cover, exercised by live Sheets only
        warnings.append(f"Jobs freeze or filter formatting was not applied: {exc}")
    return ReviewQueueResult(
        jobs_read=len(jobs),
        review_queue_rows=max(0, len(values) - 1),
        review_queue_rows_written=len(values),
        generated_at=utc_now_iso(),
        jobs_filter_applied=jobs_filter_applied,
        review_queue_filter_applied=review_filter_applied,
        review_queue_freeze_applied=review_freeze_applied,
        jobs_freeze_applied=jobs_freeze_applied,
        filter_views_created=0,
        warnings=warnings,
    )


def run_review_queue_refresh() -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_review_queue(sheet_client)
    return {"run_mode": "sprint_39_review_queue_refresh", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the Job Market Tracker Review_Queue tab")
    parser.add_argument("--refresh", action="store_true", help="Refresh Review_Queue from Jobs and apply review-friendly filters and freezes")
    return parser.parse_args()


def main() -> None:
    parse_args()
    print(json.dumps(run_review_queue_refresh(), indent=2))


if __name__ == "__main__":
    main()
