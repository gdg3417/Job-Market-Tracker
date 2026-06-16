from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Any

import requests

from src.models import JobPosting, today_iso
from src.normalize import build_job_key, normalize_url

CLOSED_STATUS_CODES = {404, 410}
CLOSURE_PHRASES = (
    "job is no longer available",
    "this job is no longer available",
    "job no longer available",
    "position is no longer available",
    "this position is no longer available",
    "opening is no longer available",
    "opportunity is no longer available",
    "posting has expired",
    "job posting has expired",
    "this job has expired",
    "position has been filled",
    "no longer accepting applications",
    "the job you are looking for is no longer open",
    "this opening is now closed",
)


@dataclass(slots=True)
class ClosureCheckResult:
    checked: bool
    is_closed: bool
    reason: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LifecycleSummary:
    records_checked: int = 0
    jobs_seen_current_run: int = 0
    jobs_not_seen: int = 0
    jobs_already_closed: int = 0
    jobs_marked_not_seen_once: int = 0
    jobs_marked_likely_closed: int = 0
    jobs_confirmed_closed: int = 0
    url_checks_attempted: int = 0
    url_checks_failed: int = 0
    rows_updated: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def ensure_job_key(job: JobPosting) -> JobPosting:
    if not job.job_key:
        job.job_key = build_job_key(job.company, job.title, job.location)
    return job


def _read_jobs_with_row_numbers(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())

    records = sheet_client.read_records("Jobs")
    jobs: list[tuple[int, JobPosting]] = []
    for index, record in enumerate(records):
        if any(str(record.get(key, "")).strip() for key in ["job_key", "company", "title", "canonical_url"]):
            jobs.append((index + 2, JobPosting.from_dict(record)))
    return jobs


def _coerce_closure_check_result(value: Any) -> ClosureCheckResult:
    if isinstance(value, ClosureCheckResult):
        return value
    if isinstance(value, bool):
        return ClosureCheckResult(checked=True, is_closed=value)
    if isinstance(value, dict):
        return ClosureCheckResult(
            checked=bool(value.get("checked")),
            is_closed=bool(value.get("is_closed")),
            reason=str(value.get("reason", "")),
            error_message=str(value.get("error_message", "")),
        )
    return ClosureCheckResult(checked=False, is_closed=False, reason="unsupported_checker_result")


def check_job_url_closed(
    job: JobPosting,
    *,
    timeout_seconds: int = 10,
    session: Any | None = None,
) -> ClosureCheckResult:
    url = normalize_url(job.canonical_url)
    if not url:
        return ClosureCheckResult(checked=False, is_closed=False, reason="missing_url")

    requester = session or requests
    headers = {"User-Agent": "job-market-tracker/1.0"}
    try:
        response = requester.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return ClosureCheckResult(checked=False, is_closed=False, reason="request_failed", error_message=str(exc))

    if response.status_code in CLOSED_STATUS_CODES:
        return ClosureCheckResult(checked=True, is_closed=True, reason=f"status_{response.status_code}")
    if response.status_code >= 500:
        return ClosureCheckResult(checked=True, is_closed=False, reason=f"server_status_{response.status_code}")

    content_type = response.headers.get("content-type", "").lower()
    if content_type and "text" not in content_type and "html" not in content_type:
        return ClosureCheckResult(checked=True, is_closed=False, reason="non_text_response")

    page_text = response.text[:300_000].lower()
    for phrase in CLOSURE_PHRASES:
        if phrase in page_text:
            return ClosureCheckResult(checked=True, is_closed=True, reason=f"closure_phrase:{phrase}")

    return ClosureCheckResult(checked=True, is_closed=False, reason="no_closure_signal")


def update_lifecycle_for_missing_jobs(
    sheet_client: Any,
    *,
    seen_job_keys: Iterable[str] | None = None,
    run_date: str | None = None,
    url_checker: Callable[[JobPosting], ClosureCheckResult | bool | dict[str, Any]] | None = None,
) -> LifecycleSummary:
    current_date = run_date or today_iso()
    explicit_seen_keys = {str(key).strip() for key in seen_job_keys or [] if str(key).strip()}
    summary = LifecycleSummary()

    for row_number, job in _read_jobs_with_row_numbers(sheet_client):
        job = ensure_job_key(job)
        summary.records_checked += 1

        was_seen_this_run = job.job_key in explicit_seen_keys or job.last_seen_date == current_date
        if was_seen_this_run:
            summary.jobs_seen_current_run += 1
            continue

        if job.status == "confirmed_closed":
            summary.jobs_already_closed += 1
            continue

        summary.jobs_not_seen += 1
        job.mark_missed(current_date)
        if job.status == "not_seen_once":
            summary.jobs_marked_not_seen_once += 1
        elif job.status == "likely_closed":
            summary.jobs_marked_likely_closed += 1

        if job.status == "likely_closed" and url_checker is not None:
            summary.url_checks_attempted += 1
            result = _coerce_closure_check_result(url_checker(job))
            if not result.checked and result.error_message:
                summary.url_checks_failed += 1
            if result.checked and result.is_closed:
                job.mark_closed(current_date)
                summary.jobs_confirmed_closed += 1

        sheet_client.update_job(row_number, job)
        summary.rows_updated += 1

    return summary
