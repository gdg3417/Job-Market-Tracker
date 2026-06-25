from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from src.enrichment.company_config import CompanyEnrichmentConfig
from src.models import JobPosting
from src.normalize import clean_text
from src.sources.greenhouse import fetch_greenhouse_board
from src.sources.lever import fetch_lever_board

ASHBY_URL_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{token}"
SMARTRECRUITERS_LIST_URL_TEMPLATE = "https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=100"
SMARTRECRUITERS_DETAIL_URL_TEMPLATE = "https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{posting_id}"
SUPPORTED_AUTOMATED_PLATFORMS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
CONFIGURED_ONLY_PLATFORMS = {
    "workday",
    "icims",
    "successfactors",
    "success factors",
    "phenom",
    "oracle",
    "oracle recruiting",
    "company_api",
    "company-specific",
}


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any:
        ...

    def raise_for_status(self) -> None:
        ...


class SessionLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> ResponseLike:
        ...


@dataclass(frozen=True, slots=True)
class AtsCandidate:
    platform: str
    posting_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description_text: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    employment_type: str = ""
    remote_status: str = "unknown"
    work_model: str = "unknown"
    posting_date: str = ""
    valid_through: str = ""


@dataclass(slots=True)
class AtsDiscoveryResult:
    platform: str
    status: str
    candidates: list[AtsCandidate] = field(default_factory=list)
    error_message: str = ""
    http_status: int | None = None
    search_url: str = ""


def _platform(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def _clean_html(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "<" not in text or ">" not in text:
        return clean_text(text)
    return clean_text(BeautifulSoup(text, "html.parser").get_text(" ", strip=True))


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _remote_fields(*values: Any) -> tuple[str, str]:
    if any(value is True or str(value or "").strip().lower() == "true" for value in values):
        return "remote", "remote"
    text = " ".join(clean_text(value).lower() for value in values if clean_text(value))
    if "hybrid" in text:
        return "hybrid", "hybrid"
    if "remote" in text or "work from home" in text:
        return "remote", "remote"
    if text:
        return "onsite", "onsite"
    return "unknown", "unknown"


def _candidate_from_job(job: JobPosting, *, platform: str, canonical_company: str) -> AtsCandidate:
    return AtsCandidate(
        platform=platform,
        posting_id=str(job.source_job_id or ""),
        title=job.title,
        company=job.company or canonical_company,
        location=job.location,
        url=job.canonical_url,
        description_text=job.description_text,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        currency=job.currency or "USD",
        remote_status=job.remote_status,
        work_model=job.work_model,
        posting_date=job.first_seen_date,
    )


def _greenhouse(config: CompanyEnrichmentConfig, *, session: SessionLike | None, timeout_seconds: int) -> AtsDiscoveryResult:
    if not config.board_token:
        return AtsDiscoveryResult("greenhouse", "invalid_config", error_message="Missing Greenhouse board token")
    result = fetch_greenhouse_board(config.to_company_row(), session=session, timeout_seconds=timeout_seconds)
    return AtsDiscoveryResult(
        platform="greenhouse",
        status=result.status,
        candidates=[_candidate_from_job(job, platform="greenhouse", canonical_company=config.canonical_name) for job in result.jobs],
        error_message=result.error_message,
        http_status=result.http_status,
        search_url=config.career_search_url,
    )


def _lever(config: CompanyEnrichmentConfig, *, session: SessionLike | None, timeout_seconds: int) -> AtsDiscoveryResult:
    if not config.board_token:
        return AtsDiscoveryResult("lever", "invalid_config", error_message="Missing Lever board token")
    result = fetch_lever_board(config.to_company_row(), session=session, timeout_seconds=timeout_seconds)
    return AtsDiscoveryResult(
        platform="lever",
        status=result.status,
        candidates=[_candidate_from_job(job, platform="lever", canonical_company=config.canonical_name) for job in result.jobs],
        error_message=result.error_message,
        http_status=result.http_status,
        search_url=config.career_search_url,
    )


def _ashby(config: CompanyEnrichmentConfig, *, session: SessionLike | None, timeout_seconds: int) -> AtsDiscoveryResult:
    token = config.board_token
    if not token:
        return AtsDiscoveryResult("ashby", "invalid_config", error_message="Missing Ashby board token")
    client = session or requests
    url = ASHBY_URL_TEMPLATE.format(token=quote(token, safe=""))
    try:
        response = client.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ashby response JSON was not an object")
        raw_jobs = payload.get("jobs") or []
        if not isinstance(raw_jobs, list):
            raise ValueError("Ashby response jobs field was not a list")
        candidates: list[AtsCandidate] = []
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            location = clean_text(raw.get("location") or raw.get("locationName"))
            remote_status, work_model = _remote_fields(raw.get("isRemote"), raw.get("workplaceType"), location)
            candidates.append(
                AtsCandidate(
                    platform="ashby",
                    posting_id=clean_text(raw.get("id") or raw.get("jobId")),
                    title=clean_text(raw.get("title")),
                    company=config.canonical_name,
                    location=location,
                    url=clean_text(raw.get("jobUrl") or raw.get("applyUrl")),
                    description_text=_clean_html(raw.get("descriptionPlain") or raw.get("descriptionHtml") or raw.get("description")),
                    employment_type=clean_text(raw.get("employmentType")),
                    remote_status=remote_status,
                    work_model=work_model,
                    posting_date=clean_text(raw.get("publishedAt") or raw.get("datePosted"))[:10],
                    valid_through=clean_text(raw.get("validThrough"))[:10],
                )
            )
        return AtsDiscoveryResult(
            platform="ashby",
            status="success" if candidates else "empty",
            candidates=candidates,
            http_status=getattr(response, "status_code", None),
            search_url=config.career_search_url,
        )
    except (requests.RequestException, ValueError) as exc:
        return AtsDiscoveryResult("ashby", "failed", error_message=str(exc), search_url=config.career_search_url)


def _smartrecruiters_sections(detail: dict[str, Any]) -> str:
    job_ad = detail.get("jobAd") or {}
    sections = job_ad.get("sections") or {}
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    for value in sections.values():
        if isinstance(value, dict):
            text = value.get("text") or value.get("description") or value.get("title")
        else:
            text = value
        clean = _clean_html(text)
        if clean:
            parts.append(clean)
    return " ".join(parts)


def _smartrecruiters_location(value: Any) -> str:
    if isinstance(value, dict):
        parts = [value.get("city"), value.get("region"), value.get("country")]
        return ", ".join(clean_text(part) for part in parts if clean_text(part))
    return clean_text(value)


def _smartrecruiters(
    config: CompanyEnrichmentConfig,
    *,
    expected_title: str,
    expected_location: str,
    session: SessionLike | None,
    timeout_seconds: int,
) -> AtsDiscoveryResult:
    company_id = config.company_identifier
    if not company_id:
        return AtsDiscoveryResult("smartrecruiters", "invalid_config", error_message="Missing SmartRecruiters company ID")
    client = session or requests
    list_url = SMARTRECRUITERS_LIST_URL_TEMPLATE.format(company_id=quote(company_id, safe=""))
    try:
        response = client.get(list_url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SmartRecruiters response JSON was not an object")
        raw_jobs = payload.get("content") or payload.get("jobs") or []
        if not isinstance(raw_jobs, list):
            raise ValueError("SmartRecruiters response content field was not a list")

        ranked: list[tuple[int, dict[str, Any]]] = []
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            title = clean_text(raw.get("name") or raw.get("title"))
            title_score = int(fuzz.token_set_ratio(expected_title, title)) if expected_title and title else 0
            location = _smartrecruiters_location(raw.get("location"))
            location_score = int(fuzz.token_set_ratio(expected_location, location)) if expected_location and location else 0
            ranked.append((title_score * 2 + location_score, raw))
        ranked.sort(key=lambda pair: pair[0], reverse=True)

        candidates: list[AtsCandidate] = []
        for _, raw in ranked[:10]:
            posting_id = clean_text(raw.get("id") or raw.get("uuid"))
            detail = raw
            if posting_id:
                detail_url = SMARTRECRUITERS_DETAIL_URL_TEMPLATE.format(
                    company_id=quote(company_id, safe=""),
                    posting_id=quote(posting_id, safe=""),
                )
                detail_response = client.get(detail_url, timeout=timeout_seconds)
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
                if isinstance(detail_payload, dict):
                    detail = detail_payload
            location = _smartrecruiters_location(detail.get("location") or raw.get("location"))
            remote_status, work_model = _remote_fields(
                detail.get("workplaceType"),
                detail.get("remote"),
                location,
            )
            compensation = detail.get("compensation") or {}
            candidates.append(
                AtsCandidate(
                    platform="smartrecruiters",
                    posting_id=posting_id,
                    title=clean_text(detail.get("name") or detail.get("title") or raw.get("name")),
                    company=clean_text((detail.get("company") or {}).get("name")) or config.canonical_name,
                    location=location,
                    url=clean_text(detail.get("postingUrl") or detail.get("applyUrl") or raw.get("ref")),
                    description_text=_smartrecruiters_sections(detail),
                    salary_min=_optional_int(compensation.get("min") if isinstance(compensation, dict) else None),
                    salary_max=_optional_int(compensation.get("max") if isinstance(compensation, dict) else None),
                    currency=clean_text(compensation.get("currency")) if isinstance(compensation, dict) else "USD",
                    employment_type=clean_text((detail.get("typeOfEmployment") or {}).get("label") if isinstance(detail.get("typeOfEmployment"), dict) else detail.get("typeOfEmployment")),
                    remote_status=remote_status,
                    work_model=work_model,
                    posting_date=clean_text(detail.get("releasedDate") or raw.get("releasedDate"))[:10],
                )
            )
        return AtsDiscoveryResult(
            platform="smartrecruiters",
            status="success" if candidates else "empty",
            candidates=candidates,
            http_status=getattr(response, "status_code", None),
            search_url=config.career_search_url,
        )
    except (requests.RequestException, ValueError) as exc:
        return AtsDiscoveryResult("smartrecruiters", "failed", error_message=str(exc), search_url=config.career_search_url)


def discover_ats_candidates(
    config: CompanyEnrichmentConfig,
    *,
    expected_title: str = "",
    expected_location: str = "",
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
) -> AtsDiscoveryResult:
    platform = _platform(config.ats_platform)
    if platform == "greenhouse":
        return _greenhouse(config, session=session, timeout_seconds=timeout_seconds)
    if platform == "lever":
        return _lever(config, session=session, timeout_seconds=timeout_seconds)
    if platform == "ashby":
        return _ashby(config, session=session, timeout_seconds=timeout_seconds)
    if platform in {"smartrecruiters", "smart recruiters"}:
        return _smartrecruiters(
            config,
            expected_title=expected_title,
            expected_location=expected_location,
            session=session,
            timeout_seconds=timeout_seconds,
        )
    if platform in CONFIGURED_ONLY_PLATFORMS or config.career_search_url:
        return AtsDiscoveryResult(
            platform=platform or "career_site",
            status="configured_only",
            error_message="No stable configured API adapter is available; the career search URL is retained for review",
            search_url=config.career_search_url,
        )
    return AtsDiscoveryResult(
        platform=platform or "unknown",
        status="invalid_config",
        error_message="Company enrichment configuration has no supported ATS platform or career search URL",
    )
