from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from src.models import JobPosting
from src.normalize import clean_text, normalize_raw_job
from src.scoring import score_job

GREENHOUSE_SOURCE = "greenhouse"
GREENHOUSE_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]:
        ...

    def raise_for_status(self) -> None:
        ...


class SessionLike(Protocol):
    def get(self, url: str, timeout: int) -> ResponseLike:
        ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_timestamp(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace(".", "").replace("+0000", "Z").replace("+00:00", "Z")


def _is_truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _source_matches_greenhouse(company_row: dict[str, Any]) -> bool:
    source_values = [
        company_row.get("source_type"),
        company_row.get("ats_platform"),
        company_row.get("source_primary"),
        company_row.get("source_url"),
    ]
    return any(GREENHOUSE_SOURCE in str(value or "").strip().lower() for value in source_values)


def normalize_greenhouse_slug(value: Any) -> str:
    text = clean_text(value).strip("/")
    if not text:
        return ""
    if "://" not in text:
        return text

    parts = urlsplit(text)
    path_parts = [part for part in parts.path.split("/") if part]
    host = parts.netloc.lower()

    if host == "boards.greenhouse.io" and path_parts:
        return path_parts[0]

    if host == "boards-api.greenhouse.io" and "boards" in path_parts:
        board_index = path_parts.index("boards")
        if len(path_parts) > board_index + 1:
            return path_parts[board_index + 1]

    return text


def greenhouse_company_rows(company_rows: list[dict[str, Any]], active_only: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in company_rows:
        if not _source_matches_greenhouse(row):
            continue
        if active_only and not _is_truthy(row.get("active"), default=True):
            continue
        if not normalize_greenhouse_slug(row.get("source_slug")):
            continue
        rows.append(row)
    return rows


def build_greenhouse_url(source_slug: Any) -> str:
    slug = normalize_greenhouse_slug(source_slug)
    if not slug:
        raise ValueError("Greenhouse source_slug is required")
    return GREENHOUSE_URL_TEMPLATE.format(slug=slug)


def extract_company_context(company_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_name": company_row.get("company_name", ""),
        "parent_company": company_row.get("parent_company", ""),
        "industry_bucket": company_row.get("industry_bucket", ""),
        "company_size_bucket": company_row.get("company_size_bucket", ""),
        "ownership_type": company_row.get("ownership_type", ""),
        "priority_tier": company_row.get("priority_tier", ""),
        "location_focus": company_row.get("location_focus", ""),
        "notes": company_row.get("notes", ""),
    }


def _extract_location(raw_job: dict[str, Any]) -> str:
    location = raw_job.get("location") or {}
    if isinstance(location, dict):
        return clean_text(location.get("name", ""))
    return clean_text(location)


def _extract_names(raw_job: dict[str, Any], field_name: str) -> str:
    values = raw_job.get(field_name) or []
    if not isinstance(values, list):
        return clean_text(values)
    names = []
    for value in values:
        if isinstance(value, dict):
            name = clean_text(value.get("name"))
            if name:
                names.append(name)
        else:
            name = clean_text(value)
            if name:
                names.append(name)
    return "; ".join(names)


def _extract_metadata_text(raw_job: dict[str, Any]) -> str:
    metadata = raw_job.get("metadata") or []
    if not isinstance(metadata, list):
        return clean_text(metadata)
    parts: list[str] = []
    for item in metadata:
        if not isinstance(item, dict):
            text = clean_text(item)
            if text:
                parts.append(text)
            continue
        label = clean_text(item.get("name") or item.get("label") or item.get("key"))
        value = item.get("value")
        if isinstance(value, list):
            value_text = ", ".join(clean_text(v) for v in value if clean_text(v))
        else:
            value_text = clean_text(value)
        if label and value_text:
            parts.append(f"{label}: {value_text}")
        elif value_text:
            parts.append(value_text)
    return " ".join(parts)


def normalize_greenhouse_job(
    raw_job: dict[str, Any],
    company_row: dict[str, Any],
    *,
    seen_date: str | None = None,
) -> JobPosting:
    department_text = _extract_names(raw_job, "departments")
    office_text = _extract_names(raw_job, "offices")
    metadata_text = _extract_metadata_text(raw_job)
    content_parts = [
        raw_job.get("content", ""),
        f"Departments: {department_text}" if department_text else "",
        f"Offices: {office_text}" if office_text else "",
        metadata_text,
    ]
    raw_normalized = {
        "company": company_row.get("company_name", ""),
        "title": raw_job.get("title", ""),
        "location": _extract_location(raw_job),
        "url": raw_job.get("absolute_url", ""),
        "source_job_id": raw_job.get("id") or raw_job.get("internal_job_id") or "",
        "description": " ".join(clean_text(part) for part in content_parts if clean_text(part)),
        "salary": metadata_text,
    }
    return normalize_raw_job(raw_normalized, source_primary=GREENHOUSE_SOURCE, seen_date=seen_date)


@dataclass(slots=True)
class GreenhouseSourceResult:
    company_name: str
    source_slug: str
    status: str
    records_found: int = 0
    jobs: list[JobPosting] = field(default_factory=list)
    error_message: str = ""
    http_status: int | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    @property
    def source_name(self) -> str:
        company = self.company_name or self.source_slug or "unknown"
        return f"greenhouse:{company}"

    def to_run_record(self) -> dict[str, Any]:
        status = self.status or "unknown"
        return {
            "run_id": f"greenhouse_{self.source_slug or 'missing_slug'}_{_run_timestamp(self.started_at)}",
            "run_type": "sprint_5_greenhouse_source",
            "source_type": GREENHOUSE_SOURCE,
            "source_name": self.source_name,
            "status": status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": 0,
            "records_found": self.records_found,
            "records_inserted": 0,
            "records_updated": 0,
            "records_failed": 0 if status in {"success", "empty"} else 1,
            "rows_read": 1,
            "config_companies_rows": 1,
            "companies_read": 1,
            "searches_read": 0,
            "error_message": self.error_message,
            "notes": f"Sprint 5 Greenhouse fetch for slug={self.source_slug}; http_status={self.http_status or ''}",
            "created_at": self.finished_at,
            "updated_at": self.finished_at,
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_slug": self.source_slug,
            "status": self.status,
            "records_found": self.records_found,
            "error_message": self.error_message,
            "http_status": self.http_status,
        }


def fetch_greenhouse_payload(
    source_slug: str,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
) -> tuple[dict[str, Any], int | None]:
    client = session or requests
    response = client.get(build_greenhouse_url(source_slug), timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Greenhouse response JSON was not an object")
    return payload, getattr(response, "status_code", None)


def fetch_greenhouse_board(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> GreenhouseSourceResult:
    started_at = utc_now_iso()
    company_name = clean_text(company_row.get("company_name", ""))
    source_slug = normalize_greenhouse_slug(company_row.get("source_slug", ""))
    if not source_slug:
        finished_at = utc_now_iso()
        return GreenhouseSourceResult(
            company_name=company_name,
            source_slug="",
            status="failed",
            error_message="Missing Greenhouse source_slug",
            started_at=started_at,
            finished_at=finished_at,
        )

    try:
        payload, http_status = fetch_greenhouse_payload(
            source_slug,
            session=session,
            timeout_seconds=timeout_seconds,
        )
        raw_jobs = payload.get("jobs") or []
        if not isinstance(raw_jobs, list):
            raise ValueError("Greenhouse response jobs field was not a list")

        company_context = extract_company_context(company_row)
        jobs: list[JobPosting] = []
        for raw_job in raw_jobs:
            if not isinstance(raw_job, dict):
                continue
            job = normalize_greenhouse_job(raw_job, company_row, seen_date=seen_date)
            if scoring_rules is not None:
                job = score_job(job, scoring_rules, company_context=company_context)
            jobs.append(job)

        finished_at = utc_now_iso()
        return GreenhouseSourceResult(
            company_name=company_name,
            source_slug=source_slug,
            status="success" if jobs else "empty",
            records_found=len(jobs),
            jobs=jobs,
            http_status=http_status,
            started_at=started_at,
            finished_at=finished_at,
        )
    except (requests.RequestException, ValueError) as exc:
        finished_at = utc_now_iso()
        return GreenhouseSourceResult(
            company_name=company_name,
            source_slug=source_slug,
            status="failed",
            error_message=str(exc),
            started_at=started_at,
            finished_at=finished_at,
        )


def fetch_greenhouse_jobs(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> list[JobPosting]:
    result = fetch_greenhouse_board(
        company_row,
        scoring_rules=scoring_rules,
        session=session,
        timeout_seconds=timeout_seconds,
        seen_date=seen_date,
    )
    return result.jobs


def append_greenhouse_run_results(sheet_client: Any, results: list[GreenhouseSourceResult]) -> int:
    appended = 0
    for result in results:
        sheet_client.append_run(result.to_run_record())
        appended += 1
    return appended


def run_greenhouse_companies(
    company_rows: list[dict[str, Any]],
    scoring_rules: dict[str, Any] | None = None,
    *,
    sheet_client: Any | None = None,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> tuple[list[JobPosting], list[GreenhouseSourceResult]]:
    jobs: list[JobPosting] = []
    results: list[GreenhouseSourceResult] = []
    for company_row in greenhouse_company_rows(company_rows):
        result = fetch_greenhouse_board(
            company_row,
            scoring_rules=scoring_rules,
            session=session,
            timeout_seconds=timeout_seconds,
            seen_date=seen_date,
        )
        jobs.extend(result.jobs)
        results.append(result)
        if sheet_client is not None:
            sheet_client.append_run(result.to_run_record())
    return jobs, results
