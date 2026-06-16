from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import requests

from src.models import JobPosting
from src.normalize import clean_text, normalize_raw_job
from src.scoring import score_job

LEVER_SOURCE = "lever"
LEVER_URL_TEMPLATE = "https://api.lever.co/v0/postings/{slug}?mode=json"


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any:
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


def _source_matches_lever(company_row: dict[str, Any]) -> bool:
    source_type = clean_text(company_row.get("source_type")).lower()
    ats_platform = clean_text(company_row.get("ats_platform")).lower()
    source_primary = clean_text(company_row.get("source_primary")).lower()
    source_url = clean_text(company_row.get("source_url")).lower()
    return any(
        [
            source_type == LEVER_SOURCE or source_type.startswith("lever"),
            ats_platform == LEVER_SOURCE or ats_platform.startswith("lever"),
            source_primary == LEVER_SOURCE,
            "lever.co" in source_url,
        ]
    )


def normalize_lever_slug(value: Any) -> str:
    text = clean_text(value).strip("/")
    if not text:
        return ""
    if "://" not in text and ".lever.co/" in text.lower():
        text = "https://" + text
    if "://" not in text:
        return text

    parts = urlsplit(text)
    path_parts = [part for part in parts.path.split("/") if part]
    host = parts.netloc.lower()

    if host in {"jobs.lever.co", "hire.lever.co"} and path_parts:
        return path_parts[0]

    if host == "api.lever.co" and "postings" in path_parts:
        postings_index = path_parts.index("postings")
        if len(path_parts) > postings_index + 1:
            return path_parts[postings_index + 1]

    if path_parts:
        return path_parts[0]
    return text


def lever_company_rows(company_rows: list[dict[str, Any]], active_only: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in company_rows:
        if not _source_matches_lever(row):
            continue
        if active_only and not _is_truthy(row.get("active"), default=True):
            continue
        if not normalize_lever_slug(row.get("source_slug")):
            continue
        rows.append(row)
    return rows


def build_lever_url(source_slug: Any) -> str:
    slug = normalize_lever_slug(source_slug)
    if not slug:
        raise ValueError("Lever source_slug is required")
    return LEVER_URL_TEMPLATE.format(slug=quote(slug, safe=""))


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


def _extract_categories(raw_job: dict[str, Any]) -> dict[str, Any]:
    categories = raw_job.get("categories") or {}
    return categories if isinstance(categories, dict) else {}


def _extract_location(raw_job: dict[str, Any]) -> str:
    categories = _extract_categories(raw_job)
    return clean_text(categories.get("location") or raw_job.get("location") or raw_job.get("locations"))


def _extract_lists_text(raw_job: dict[str, Any]) -> str:
    lists = raw_job.get("lists") or []
    if not isinstance(lists, list):
        return clean_text(lists)

    parts: list[str] = []
    for item in lists:
        if not isinstance(item, dict):
            text = clean_text(item)
            if text:
                parts.append(text)
            continue
        label = clean_text(item.get("text") or item.get("label") or item.get("title"))
        content = clean_text(item.get("content") or item.get("description"))
        if label and content:
            parts.append(f"{label}: {content}")
        elif content:
            parts.append(content)
        elif label:
            parts.append(label)
    return " ".join(parts)


def _extract_salary_text(raw_job: dict[str, Any], fallback_text: str = "") -> str:
    salary_range = raw_job.get("salaryRange") or raw_job.get("salary_range") or raw_job.get("compensationRange")
    if isinstance(salary_range, dict):
        minimum = salary_range.get("min") or salary_range.get("minimum")
        maximum = salary_range.get("max") or salary_range.get("maximum")
        currency = salary_range.get("currency") or salary_range.get("currencyCode") or "USD"
        interval = salary_range.get("interval") or salary_range.get("period") or ""
        values = [clean_text(value) for value in [minimum, maximum] if clean_text(value)]
        if values:
            joined = " - ".join(values)
            return clean_text(f"{currency} {joined} {interval}")
    return fallback_text


def normalize_lever_job(
    raw_job: dict[str, Any],
    company_row: dict[str, Any],
    *,
    seen_date: str | None = None,
) -> JobPosting:
    categories = _extract_categories(raw_job)
    department = clean_text(categories.get("department"))
    team = clean_text(categories.get("team"))
    commitment = clean_text(categories.get("commitment"))
    level = clean_text(categories.get("level"))
    location = _extract_location(raw_job)
    hosted_url = raw_job.get("hostedUrl") or raw_job.get("hosted_url") or raw_job.get("applyUrl") or raw_job.get("url")
    lists_text = _extract_lists_text(raw_job)

    content_parts = [
        raw_job.get("descriptionPlain") or raw_job.get("description") or "",
        raw_job.get("additionalPlain") or raw_job.get("additional") or "",
        lists_text,
        f"Department: {department}" if department else "",
        f"Team: {team}" if team else "",
        f"Commitment: {commitment}" if commitment else "",
        f"Level: {level}" if level else "",
        f"Location: {location}" if location else "",
        f"Hosted URL: {hosted_url}" if hosted_url else "",
    ]
    description_text = " ".join(clean_text(part) for part in content_parts if clean_text(part))
    salary_text = _extract_salary_text(raw_job, description_text)

    raw_normalized = {
        "company": company_row.get("company_name", ""),
        "title": raw_job.get("text") or raw_job.get("title") or "",
        "location": location,
        "url": hosted_url,
        "source_job_id": raw_job.get("id") or raw_job.get("requisitionId") or raw_job.get("requisition_id") or "",
        "description": description_text,
        "salary": salary_text,
    }
    return normalize_raw_job(raw_normalized, source_primary=LEVER_SOURCE, seen_date=seen_date)


@dataclass(slots=True)
class LeverSourceResult:
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
        return f"lever:{company}"

    def to_run_record(self) -> dict[str, Any]:
        status = self.status or "unknown"
        return {
            "run_id": f"lever_{self.source_slug or 'missing_slug'}_{_run_timestamp(self.started_at)}",
            "run_type": "sprint_6_lever_source",
            "source_type": LEVER_SOURCE,
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
            "notes": f"Sprint 6 Lever fetch for slug={self.source_slug}; http_status={self.http_status or ''}",
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


def fetch_lever_payload(
    source_slug: str,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
) -> tuple[list[dict[str, Any]], int | None]:
    client = session or requests
    response = client.get(build_lever_url(source_slug), timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        raw_jobs = payload
    elif isinstance(payload, dict):
        raw_jobs = payload.get("postings") or payload.get("jobs") or payload.get("data") or []
    else:
        raise ValueError("Lever response JSON was not a list or object")

    if not isinstance(raw_jobs, list):
        raise ValueError("Lever response postings field was not a list")
    return [job for job in raw_jobs if isinstance(job, dict)], getattr(response, "status_code", None)


def fetch_lever_board(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> LeverSourceResult:
    started_at = utc_now_iso()
    company_name = clean_text(company_row.get("company_name", ""))
    source_slug = normalize_lever_slug(company_row.get("source_slug", ""))
    if not source_slug:
        finished_at = utc_now_iso()
        return LeverSourceResult(
            company_name=company_name,
            source_slug="",
            status="failed",
            error_message="Missing Lever source_slug",
            started_at=started_at,
            finished_at=finished_at,
        )

    try:
        raw_jobs, http_status = fetch_lever_payload(
            source_slug,
            session=session,
            timeout_seconds=timeout_seconds,
        )
        company_context = extract_company_context(company_row)
        jobs: list[JobPosting] = []
        for raw_job in raw_jobs:
            job = normalize_lever_job(raw_job, company_row, seen_date=seen_date)
            if scoring_rules is not None:
                job = score_job(job, scoring_rules, company_context=company_context)
            jobs.append(job)

        finished_at = utc_now_iso()
        return LeverSourceResult(
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
        return LeverSourceResult(
            company_name=company_name,
            source_slug=source_slug,
            status="failed",
            error_message=str(exc),
            started_at=started_at,
            finished_at=finished_at,
        )


def fetch_lever_jobs(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> list[JobPosting]:
    result = fetch_lever_board(
        company_row,
        scoring_rules=scoring_rules,
        session=session,
        timeout_seconds=timeout_seconds,
        seen_date=seen_date,
    )
    return result.jobs


def append_lever_run_results(sheet_client: Any, results: list[LeverSourceResult]) -> int:
    appended = 0
    for result in results:
        sheet_client.append_run(result.to_run_record())
        appended += 1
    return appended


def run_lever_companies(
    company_rows: list[dict[str, Any]],
    scoring_rules: dict[str, Any] | None = None,
    *,
    sheet_client: Any | None = None,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> tuple[list[JobPosting], list[LeverSourceResult]]:
    jobs: list[JobPosting] = []
    results: list[LeverSourceResult] = []
    for company_row in lever_company_rows(company_rows):
        result = fetch_lever_board(
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
