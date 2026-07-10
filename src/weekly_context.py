from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from src.follow_up import evaluate_follow_up
from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.review_queue import should_include_review_queue_job, sort_review_queue_jobs
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff
from src.weekly_value import (
    WEEKLY_VALUE_HEADERS,
    WEEKLY_VALUE_SHEET,
    _is_auto_rejected_job,
    _is_blocked_company_job,
    _is_strong_fit,
    _is_stretch_fit,
    _is_too_senior_job,
    _visible_score,
)
from src.weekly_value_sheet_dates import JOB_DATE_FIELDS, normalize_record_dates, normalize_sheet_date

WEEKLY_CONTEXT_SHEET = "Weekly_Context"
WEEKLY_CONTEXT_HEADERS = [
    "section",
    "item_type",
    "label",
    "value",
    "company",
    "title",
    "fit_type",
    "status",
    "reason",
    "canonical_url",
    "source_sheet",
    "source_row",
    "week_start",
    "week_end",
]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "weekly_digest.yml"
CORE_METRICS = [
    "Jobs Added",
    "Jobs Reviewed",
    "Jobs Still Not Reviewed Yet",
    "Jobs Applied",
    "Follow-ups Due",
    "Strong Fit Jobs",
    "Stretch Fit Jobs",
]
NOISE_METRICS = ["Auto-Rejected Jobs", "Blocked Company Rejects"]
DASHBOARD_ONLY_METRICS = [
    "Jobs Dismissed",
    "Jobs Moved to Active Status",
    "Outstanding Active Roles",
    "Too-Senior Rejects or Penalties",
    "Review Completion Rate",
    "Actionable Conversion Rate",
    "Dismissal Rate",
    "Backlog Change",
    "Signal Quality",
    "Noise Removed",
]
REVIEW_STATUSES = {"", "not reviewed", "not reviewed yet", "not_reviewed", "review now", "review_now", "reviewing"}
TERMINAL_JOB_STATUSES = {"confirmed closed", "closed", "expired"}


@dataclass(frozen=True, slots=True)
class WeeklyDigestConfig:
    summary_week: str = "latest_completed"
    top_review_limit: int = 5
    top_follow_up_limit: int = 5
    top_new_match_limit: int = 5
    include_dashboard_only_metrics: bool = False
    include_optional_metrics: tuple[str, ...] = ()


@dataclass(slots=True)
class WeeklyContextResult:
    jobs_read: int
    summary_week_start: str
    summary_week_end: str
    metrics_included: int
    review_items: int
    follow_up_items: int
    new_match_items: int
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


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    return default


def load_weekly_digest_config(path: str | Path | None = None) -> WeeklyDigestConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return WeeklyDigestConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    values = raw.get("weekly_digest", raw)
    optional_metrics = tuple(
        metric
        for metric in values.get("include_optional_metrics", [])
        if metric in WEEKLY_VALUE_HEADERS and metric not in {"Week Start", "Week End", "Notes"}
    )
    summary_week = str(values.get("summary_week", "latest_completed")).strip().lower()
    if summary_week not in {"latest_completed", "current", "latest_available"}:
        summary_week = "latest_completed"
    return WeeklyDigestConfig(
        summary_week=summary_week,
        top_review_limit=_positive_int(values.get("top_review_limit"), 5),
        top_follow_up_limit=_positive_int(values.get("top_follow_up_limit"), 5),
        top_new_match_limit=_positive_int(values.get("top_new_match_limit"), 5),
        include_dashboard_only_metrics=_as_bool(values.get("include_dashboard_only_metrics")),
        include_optional_metrics=optional_metrics,
    )


def _normalized_weekly_record(record: dict[str, Any]) -> dict[str, Any]:
    by_key = {_normalize(key): value for key, value in record.items()}
    normalized = {header: by_key.get(_normalize(header), "") for header in WEEKLY_VALUE_HEADERS}
    normalized["Week Start"] = normalize_sheet_date(normalized["Week Start"])
    normalized["Week End"] = normalize_sheet_date(normalized["Week End"])
    return normalized


def _previous_week(as_of_date: date) -> tuple[date, date]:
    current_start = as_of_date - timedelta(days=as_of_date.weekday())
    start = current_start - timedelta(days=7)
    return start, start + timedelta(days=6)


def _empty_week_record(as_of_date: date, mode: str) -> dict[str, Any]:
    if mode == "current":
        start = as_of_date - timedelta(days=as_of_date.weekday())
        end = start + timedelta(days=6)
    else:
        start, end = _previous_week(as_of_date)
    record = {header: 0 for header in WEEKLY_VALUE_HEADERS}
    record["Week Start"] = start.isoformat()
    record["Week End"] = end.isoformat()
    record["Notes"] = "No Weekly_Value row was available for the selected summary period."
    return record


def select_summary_record(
    weekly_records: list[dict[str, Any]],
    *,
    as_of: str | date | None = None,
    mode: str = "latest_completed",
) -> dict[str, Any]:
    as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso()) or date.today()
    parsed: list[tuple[date, date, dict[str, Any]]] = []
    for raw in weekly_records:
        record = _normalized_weekly_record(raw)
        start = parse_iso_date(record.get("Week Start"))
        end = parse_iso_date(record.get("Week End"))
        if start is not None and end is not None:
            parsed.append((start, end, record))
    if not parsed:
        return _empty_week_record(as_of_date, mode)
    parsed.sort(key=lambda item: (item[1], item[0]), reverse=True)
    if mode == "current":
        current_start = as_of_date - timedelta(days=as_of_date.weekday())
        candidates = [item for item in parsed if item[0] == current_start]
    elif mode == "latest_available":
        candidates = [item for item in parsed if item[0] <= as_of_date]
    else:
        candidates = [item for item in parsed if item[1] < as_of_date]
    return (candidates or parsed)[0][2]


def _job_identity(job: JobPosting) -> str:
    return str(job.job_key or "|".join([job.company, job.title, job.canonical_url])).strip().lower()


def _is_terminal(job: JobPosting) -> bool:
    return _normalize(job.status) in TERMINAL_JOB_STATUSES


def _in_summary_week(job: JobPosting, start: date, end: date) -> bool:
    first_seen = parse_iso_date(job.first_seen_date)
    return first_seen is not None and start <= first_seen <= end


def _fit_type(job: JobPosting) -> str:
    if _is_strong_fit(job):
        return "Strong Fit"
    if _is_stretch_fit(job):
        return "Stretch Fit"
    return ""


def _status(job: JobPosting) -> str:
    return str(job.review_status or job.application_status or job.status or "").strip()


def _reason(job: JobPosting) -> str:
    fit = _fit_type(job)
    if fit:
        return fit
    if job.potential_priority:
        return f"Potential priority: {job.potential_priority}"
    return str(job.score_explanation or "").strip()


def select_new_match_items(
    jobs_with_rows: list[tuple[int, JobPosting]],
    *,
    start: date,
    end: date,
    limit: int,
) -> list[tuple[int, JobPosting]]:
    candidates = [
        (row_number, job)
        for row_number, job in jobs_with_rows
        if _in_summary_week(job, start, end)
        and not _is_terminal(job)
        and not _is_auto_rejected_job(job)
        and not _is_blocked_company_job(job)
        and not _is_too_senior_job(job)
        and (_is_strong_fit(job) or _is_stretch_fit(job))
    ]
    candidates.sort(
        key=lambda item: (
            0 if _is_strong_fit(item[1]) else 1,
            -int(_visible_score(item[1]) or 0),
            -int(item[1].potential_priority_score or 0),
            str(item[1].company or "").lower(),
            str(item[1].title or "").lower(),
        )
    )
    return candidates[:limit]


def select_review_items(
    jobs_with_rows: list[tuple[int, JobPosting]],
    *,
    limit: int,
    excluded_job_keys: set[str] | None = None,
) -> list[tuple[int, JobPosting]]:
    excluded = excluded_job_keys or set()
    eligible: list[tuple[int, JobPosting]] = []
    for row_number, job in jobs_with_rows:
        if _job_identity(job) in excluded or _is_terminal(job):
            continue
        if _normalize(job.review_status) not in REVIEW_STATUSES:
            continue
        if _is_auto_rejected_job(job) or _is_blocked_company_job(job) or _is_too_senior_job(job):
            continue
        if should_include_review_queue_job(job):
            eligible.append((row_number, job))
    by_identity = {_job_identity(job): (row_number, job) for row_number, job in eligible}
    ordered = sort_review_queue_jobs([job for _, job in eligible])
    return [by_identity[_job_identity(job)] for job in ordered[:limit]]


def select_follow_up_items(
    jobs_with_rows: list[tuple[int, JobPosting]],
    *,
    as_of: str | date | None = None,
    limit: int,
) -> list[tuple[int, JobPosting, Any]]:
    due: list[tuple[int, JobPosting, Any]] = []
    for row_number, job in jobs_with_rows:
        evaluation = evaluate_follow_up(job, as_of=as_of)
        if evaluation.follow_up_due:
            due.append((row_number, job, evaluation))
    due.sort(
        key=lambda item: (
            0 if item[2].days_since_status_update is None else 1,
            -(item[2].days_since_status_update or 0),
            str(item[1].company or "").lower(),
            str(item[1].title or "").lower(),
        )
    )
    return due[:limit]


def _context_row(
    *,
    section: str,
    item_type: str,
    label: str = "",
    value: Any = "",
    job: JobPosting | None = None,
    fit_type: str = "",
    status: str = "",
    reason: str = "",
    source_row: int | str = "",
    week_start: str = "",
    week_end: str = "",
) -> dict[str, Any]:
    return {
        "section": section,
        "item_type": item_type,
        "label": label,
        "value": value,
        "company": job.company if job else "",
        "title": job.title if job else "",
        "fit_type": fit_type,
        "status": status,
        "reason": reason,
        "canonical_url": job.canonical_url if job else "",
        "source_sheet": "Jobs" if job else WEEKLY_VALUE_SHEET,
        "source_row": source_row,
        "week_start": week_start,
        "week_end": week_end,
    }


def _metric_names(config: WeeklyDigestConfig) -> list[str]:
    names = list(CORE_METRICS)
    if config.include_dashboard_only_metrics:
        names.extend(DASHBOARD_ONLY_METRICS)
    for metric in config.include_optional_metrics:
        if metric not in names and metric not in NOISE_METRICS:
            names.append(metric)
    return names


def build_weekly_context_rows(
    jobs_with_rows: list[tuple[int, JobPosting]],
    weekly_records: list[dict[str, Any]],
    *,
    as_of: str | date | None = None,
    config: WeeklyDigestConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or WeeklyDigestConfig()
    summary = select_summary_record(weekly_records, as_of=as_of, mode=config.summary_week)
    week_start = str(summary.get("Week Start") or "")
    week_end = str(summary.get("Week End") or "")
    start = parse_iso_date(week_start)
    end = parse_iso_date(week_end)
    if start is None or end is None:
        as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso()) or date.today()
        start, end = _previous_week(as_of_date)
        week_start, week_end = start.isoformat(), end.isoformat()

    rows = [
        _context_row(
            section="Metadata",
            item_type="period",
            label="Summary Week",
            value=f"{week_start} through {week_end}",
            week_start=week_start,
            week_end=week_end,
        )
    ]

    new_matches = select_new_match_items(
        jobs_with_rows,
        start=start,
        end=end,
        limit=config.top_new_match_limit,
    )
    new_match_keys = {_job_identity(job) for _, job in new_matches}
    review_items = select_review_items(
        jobs_with_rows,
        limit=config.top_review_limit,
        excluded_job_keys=new_match_keys,
    )
    follow_up_items = select_follow_up_items(
        jobs_with_rows,
        as_of=as_of,
        limit=config.top_follow_up_limit,
    )

    for row_number, job in review_items:
        rows.append(
            _context_row(
                section="Action Needed",
                item_type="review",
                label="Role needing review",
                job=job,
                fit_type=_fit_type(job),
                status=_status(job),
                reason=_reason(job),
                source_row=row_number,
                week_start=week_start,
                week_end=week_end,
            )
        )

    for row_number, job in new_matches:
        rows.append(
            _context_row(
                section="New Strong Matches",
                item_type="match",
                label="New fit",
                job=job,
                fit_type=_fit_type(job),
                status=_status(job),
                reason=_reason(job),
                source_row=row_number,
                week_start=week_start,
                week_end=week_end,
            )
        )

    for metric in _metric_names(config):
        rows.append(
            _context_row(
                section="Weekly Tracker Metrics",
                item_type="metric",
                label=metric,
                value=summary.get(metric, 0),
                week_start=week_start,
                week_end=week_end,
            )
        )

    for row_number, job, evaluation in follow_up_items:
        rows.append(
            _context_row(
                section="Backlog and Follow-up",
                item_type="follow_up",
                label="Follow-up due",
                value=evaluation.days_since_status_update if evaluation.days_since_status_update is not None else "",
                job=job,
                fit_type=_fit_type(job),
                status=evaluation.outstanding_status,
                reason=evaluation.follow_up_reason,
                source_row=row_number,
                week_start=week_start,
                week_end=week_end,
            )
        )

    for metric in NOISE_METRICS:
        rows.append(
            _context_row(
                section="Noise Removed",
                item_type="metric",
                label=metric,
                value=summary.get(metric, 0),
                week_start=week_start,
                week_end=week_end,
            )
        )
    return rows


def build_weekly_context_values(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [WEEKLY_CONTEXT_HEADERS, *[[row.get(header, "") for header in WEEKLY_CONTEXT_HEADERS] for row in rows]]


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
    metadata = with_quota_backoff(
        lambda: sheet_client.workbook.fetch_sheet_metadata(),
        operation_name="fetch workbook metadata",
    )
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties") or {}
        if properties.get("title") == WEEKLY_CONTEXT_SHEET:
            return int(properties["sheetId"])
    raise ValueError(f"Could not resolve worksheet id for {WEEKLY_CONTEXT_SHEET}")


def _formatting_requests(sheet_id: int, row_count: int) -> list[dict[str, Any]]:
    column_count = len(WEEKLY_CONTEXT_HEADERS)
    return [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2},
                },
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
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
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
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(2, row_count),
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": column_count,
                }
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 8,
                    "endIndex": 9,
                },
                "properties": {"pixelSize": 420},
                "fields": "pixelSize",
            }
        },
    ]


def write_weekly_context(sheet_client: SheetClient, values: list[list[Any]]) -> Any:
    worksheet = sheet_client.ensure_worksheet(
        WEEKLY_CONTEXT_SHEET,
        rows=max(1000, len(values) + 10),
        cols=len(WEEKLY_CONTEXT_HEADERS),
    )
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {WEEKLY_CONTEXT_SHEET}")
    end_cell = f"{_column_name(len(WEEKLY_CONTEXT_HEADERS))}{len(values)}"
    with_quota_backoff(
        lambda: worksheet.update(
            range_name=f"A1:{end_cell}",
            values=values,
            value_input_option="USER_ENTERED",
        ),
        operation_name=f"write worksheet {WEEKLY_CONTEXT_SHEET}",
    )
    return worksheet


def apply_weekly_context(
    sheet_client: SheetClient,
    *,
    as_of: str | date | None = None,
    jobs_with_rows: list[tuple[int, JobPosting]] | None = None,
    weekly_records: list[dict[str, Any]] | None = None,
    config: WeeklyDigestConfig | None = None,
) -> WeeklyContextResult:
    jobs_with_rows = jobs_with_rows if jobs_with_rows is not None else sheet_client.read_jobs_with_row_numbers()
    weekly_records = weekly_records if weekly_records is not None else sheet_client.read_records(WEEKLY_VALUE_SHEET)
    rows = build_weekly_context_rows(jobs_with_rows, weekly_records, as_of=as_of, config=config)
    values = build_weekly_context_values(rows)
    worksheet = write_weekly_context(sheet_client, values)
    warnings: list[str] = []
    try:
        requests = _formatting_requests(_worksheet_id(sheet_client, worksheet), len(values))
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name=f"format worksheet {WEEKLY_CONTEXT_SHEET}",
        )
    except Exception as exc:
        warnings.append(f"Weekly_Context formatting was not applied: {exc}")

    period = next((row for row in rows if row["item_type"] == "period"), {})
    return WeeklyContextResult(
        jobs_read=len(jobs_with_rows),
        summary_week_start=str(period.get("week_start") or ""),
        summary_week_end=str(period.get("week_end") or ""),
        metrics_included=sum(1 for row in rows if row["item_type"] == "metric"),
        review_items=sum(1 for row in rows if row["item_type"] == "review"),
        follow_up_items=sum(1 for row in rows if row["item_type"] == "follow_up"),
        new_match_items=sum(1 for row in rows if row["item_type"] == "match"),
        rows_written=len(values),
        generated_at=utc_now_iso(),
        warnings=warnings,
    )


def _has_job_identity(record: dict[str, Any]) -> bool:
    return any(str(record.get(key, "")).strip() for key in ("job_key", "company", "title", "canonical_url"))


def run_weekly_context_refresh(
    *,
    as_of: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    raw_jobs = sheet_client.read_records("Jobs")
    jobs_with_rows = [
        (index + 2, JobPosting.from_dict(normalize_record_dates(record, JOB_DATE_FIELDS)))
        for index, record in enumerate(raw_jobs)
        if _has_job_identity(record)
    ]
    weekly_records = sheet_client.read_records(WEEKLY_VALUE_SHEET)
    result = apply_weekly_context(
        sheet_client,
        as_of=as_of,
        jobs_with_rows=jobs_with_rows,
        weekly_records=weekly_records,
        config=load_weekly_digest_config(config_path),
    )
    return {"run_mode": "sprint_45_weekly_context_refresh", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the concise Weekly_Context email contract")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_weekly_context_refresh(as_of=args.as_of, config_path=args.config), indent=2))


if __name__ == "__main__":
    main()
