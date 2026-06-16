from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from src.models import JobPosting
from src.normalize import clean_text, normalize_raw_job, normalize_url
from src.scoring import score_job

STATIC_PAGE_SOURCE = "static_page"
DEFAULT_INCLUDE_TERMS = [
    "commercial strategy",
    "revenue strategy",
    "business operations",
    "strategy operations",
    "business insights",
    "corporate strategy",
    "product line",
    "category management",
    "segment management",
    "commercial operations",
    "sales operations",
    "chief of staff",
    "general manager",
    "business unit",
    "pricing strategy",
    "go-to-market",
    "gtm",
    "value creation",
    "finance transformation",
]
DEFAULT_EXCLUDE_TERMS = [
    "staff accountant",
    "senior accountant",
    "cost accountant",
    "payroll",
    "tax",
    "audit",
    "sox",
    "sec reporting",
    "data engineer",
    "bi developer",
    "scrum master",
    "financial advisor",
    "insurance sales",
    "commission-only",
]
JOB_PATH_TERMS = (
    "job",
    "jobs",
    "career",
    "careers",
    "opening",
    "openings",
    "position",
    "positions",
    "requisition",
    "posting",
    "employment",
)
GENERIC_LINK_TEXT = {
    "apply",
    "apply now",
    "careers",
    "career opportunities",
    "current openings",
    "current jobs",
    "details",
    "job details",
    "learn more",
    "open positions",
    "read more",
    "see all jobs",
    "view job",
    "view jobs",
    "view opening",
    "view position",
}
IGNORED_HOST_TERMS = (
    "linkedin.com",
    "indeed.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
)
IGNORED_PATH_TERMS = (
    "benefits",
    "blog",
    "candidate-login",
    "culture",
    "diversity",
    "employee-login",
    "events",
    "faq",
    "home",
    "internship-program",
    "life-at",
    "login",
    "media",
    "news",
    "privacy",
    "profile",
    "signin",
    "sign-in",
    "talent-community",
    "terms",
)
IGNORED_EXTENSIONS = (
    ".7z",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
)
ROLE_LEVEL_TERMS = (
    "director",
    "senior manager",
    "sr manager",
    "manager",
    "chief of staff",
    "head of",
    "vp",
    "vice president",
    "general manager",
)
SPLIT_RE = re.compile(r"[,;|\n\r]+")


class ResponseLike(Protocol):
    status_code: int
    text: str

    def raise_for_status(self) -> None:
        ...


class SessionLike(Protocol):
    def get(self, url: str, timeout: int, headers: dict[str, str] | None = None) -> ResponseLike:
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


def _source_text(company_row: dict[str, Any]) -> str:
    return " ".join(
        clean_text(company_row.get(field_name, "")).lower()
        for field_name in ["source_type", "ats_platform", "source_primary", "source_url", "source_slug"]
    )


def _source_matches_static_page(company_row: dict[str, Any]) -> bool:
    source_url = clean_text(company_row.get("source_url"))
    if not source_url:
        return False
    text = _source_text(company_row)
    if "greenhouse" in text or "lever" in text:
        return False
    if any(blocked in text for blocked in ["linkedin", "indeed"]):
        return False
    explicit_static_terms = [
        "static",
        "static_page",
        "career_page",
        "careers_page",
        "company_career",
        "custom",
        "custom_ats",
        "career site",
        "careers site",
    ]
    return any(term in text for term in explicit_static_terms) or any(term in source_url.lower() for term in ["career", "job"])


def static_page_company_rows(company_rows: list[dict[str, Any]], active_only: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in company_rows:
        if active_only and not _is_truthy(row.get("active"), default=True):
            continue
        if _source_matches_static_page(row):
            rows.append(row)
    return rows


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


def split_keyword_terms(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    text = re.sub(r"\bOR\b", "|", text, flags=re.IGNORECASE)
    terms = []
    for raw_term in SPLIT_RE.split(text):
        term = raw_term.strip().strip('"\'')
        if term:
            terms.append(term)
    return terms


def search_filter_terms(search_rows: list[dict[str, Any]] | None = None) -> tuple[list[str], list[str]]:
    include_terms: list[str] = []
    exclude_terms: list[str] = list(DEFAULT_EXCLUDE_TERMS)
    for row in search_rows or []:
        if not _is_truthy(row.get("active"), default=True):
            continue
        include_terms.extend(split_keyword_terms(row.get("include_keywords")))
        exclude_terms.extend(split_keyword_terms(row.get("exclude_keywords")))
    if not include_terms:
        include_terms = list(DEFAULT_INCLUDE_TERMS)
    return _dedupe_terms(include_terms), _dedupe_terms(exclude_terms)


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(value.strip())
    return deduped


def _contains_term(text: str, term: str) -> bool:
    normalized = term.strip().lower()
    if not normalized:
        return False
    if re.match(r"^[a-z0-9 &/+-]+$", normalized):
        pattern = r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return normalized in text


def _matching_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _contains_term(text, term)]


def _looks_like_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _ignored_url(url: str) -> bool:
    parts = urlsplit(url)
    lowered_url = url.lower()
    lowered_path = parts.path.lower()
    if parts.scheme not in {"http", "https"}:
        return True
    if any(host_term in parts.netloc.lower() for host_term in IGNORED_HOST_TERMS):
        return True
    if any(lowered_path.endswith(extension) for extension in IGNORED_EXTENSIONS):
        return True
    if any(path_term in lowered_path for path_term in IGNORED_PATH_TERMS):
        return True
    if lowered_url.startswith(("mailto:", "tel:", "javascript:")):
        return True
    return False


def _title_from_url(url: str) -> str:
    path_parts = [part for part in urlsplit(url).path.split("/") if part]
    if not path_parts:
        return ""
    candidate = path_parts[-1]
    candidate = re.sub(r"[-_]+", " ", candidate)
    candidate = re.sub(r"\b\d{4,}\b", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if candidate.lower() in JOB_PATH_TERMS or len(candidate) < 4:
        return ""
    return candidate.title()


def _best_title_from_anchor(anchor: Any, url: str) -> str:
    anchor_text = clean_text(anchor.get_text(" ", strip=True))
    if anchor_text and anchor_text.lower() not in GENERIC_LINK_TEXT:
        return anchor_text

    parent = anchor.find_parent(["li", "tr", "article", "section", "div"])
    if parent is not None:
        parent_text = clean_text(parent.get_text(" ", strip=True))
        if parent_text and parent_text.lower() not in GENERIC_LINK_TEXT:
            return parent_text[:250]

    url_title = _title_from_url(url)
    return url_title or anchor_text


def _link_job_score(title: str, url: str, include_matches: list[str]) -> tuple[int, list[str]]:
    evidence: list[str] = []
    score = 0
    text = f"{title} {url}".lower()
    path_parts = [part for part in urlsplit(url).path.lower().split("/") if part]

    if any(part in JOB_PATH_TERMS for part in path_parts):
        score += 2
        evidence.append("job_path")
    if re.search(r"\b(req|requisition|job|posting)[-/]?[a-z0-9]*\d{3,}\b", text):
        score += 2
        evidence.append("posting_id")
    if any(_contains_term(text, term) for term in ROLE_LEVEL_TERMS):
        score += 2
        evidence.append("role_level")
    if include_matches:
        score += 3
        evidence.extend([f"include:{term}" for term in include_matches[:3]])
    if len(title) >= 18 and title.lower() not in GENERIC_LINK_TEXT:
        score += 1
        evidence.append("specific_title")
    if len(path_parts) <= 1 and title.lower() in GENERIC_LINK_TEXT:
        score -= 4
        evidence.append("generic_listing")
    return score, evidence


def _confidence_from_score(score: int, title: str, include_matches: list[str]) -> str:
    if score >= 7 and include_matches:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def _candidate_source_id(url: str) -> str:
    normalized = normalize_url(url)
    return normalized or url


@dataclass(slots=True)
class StaticPageCandidate:
    title: str
    url: str
    location: str
    confidence: str
    score: int
    evidence: list[str] = field(default_factory=list)

    @property
    def requires_manual_review(self) -> bool:
        return self.confidence == "low"


def extract_static_page_candidates(
    html: str,
    source_url: str,
    *,
    company_row: dict[str, Any],
    search_rows: list[dict[str, Any]] | None = None,
) -> list[StaticPageCandidate]:
    include_terms, exclude_terms = search_filter_terms(search_rows)
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[StaticPageCandidate] = []
    seen_urls: set[str] = set()
    location = clean_text(company_row.get("location_focus"))

    for anchor in soup.find_all("a", href=True):
        raw_href = clean_text(anchor.get("href"))
        if not raw_href or raw_href.startswith("#"):
            continue
        url = normalize_url(urljoin(source_url, raw_href))
        if not url or url in seen_urls or not _looks_like_http_url(url) or _ignored_url(url):
            continue

        title = _best_title_from_anchor(anchor, url)
        if not title:
            continue

        combined = f"{title} {url}".lower()
        if _matching_terms(combined, exclude_terms):
            continue
        include_matches = _matching_terms(combined, include_terms)
        score, evidence = _link_job_score(title, url, include_matches)
        if score < 3 and not include_matches:
            continue

        seen_urls.add(url)
        confidence = _confidence_from_score(score, title, include_matches)
        candidates.append(
            StaticPageCandidate(
                title=title,
                url=url,
                location=location,
                confidence=confidence,
                score=score,
                evidence=evidence,
            )
        )

    return candidates


def candidate_to_job(candidate: StaticPageCandidate, company_row: dict[str, Any], *, seen_date: str | None = None) -> JobPosting:
    review_flag = "manual_review=true" if candidate.requires_manual_review else "manual_review=false"
    evidence = ", ".join(candidate.evidence[:6])
    description = clean_text(
        " ".join(
            [
                f"Static extraction confidence: {candidate.confidence}.",
                review_flag,
                f"Static link score: {candidate.score}.",
                f"Evidence: {evidence}." if evidence else "",
                "Static company career page extraction. Review manually before applying." if candidate.requires_manual_review else "Static company career page extraction.",
            ]
        )
    )
    return normalize_raw_job(
        {
            "company": company_row.get("company_name", ""),
            "title": candidate.title,
            "location": candidate.location,
            "url": candidate.url,
            "source_job_id": _candidate_source_id(candidate.url),
            "description": description,
        },
        source_primary=STATIC_PAGE_SOURCE,
        seen_date=seen_date,
    )


def mark_static_confidence(job: JobPosting, candidate: StaticPageCandidate) -> JobPosting:
    evidence = ", ".join(candidate.evidence[:6])
    suffix_parts = [
        f"static_confidence={candidate.confidence}",
        f"static_link_score={candidate.score}",
        "manual_review=true" if candidate.requires_manual_review else "manual_review=false",
    ]
    if evidence:
        suffix_parts.append(f"static_evidence={evidence}")
    suffix = "; ".join(suffix_parts)
    job.score_explanation = f"{job.score_explanation}; {suffix}" if job.score_explanation else suffix
    return job


@dataclass(slots=True)
class StaticPageSourceResult:
    company_name: str
    source_url: str
    status: str
    records_found: int = 0
    jobs: list[JobPosting] = field(default_factory=list)
    low_confidence_count: int = 0
    error_message: str = ""
    http_status: int | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    @property
    def source_name(self) -> str:
        company = self.company_name or urlsplit(self.source_url).netloc or "unknown"
        return f"static_page:{company}"

    @property
    def source_slug(self) -> str:
        host = urlsplit(self.source_url).netloc.lower().replace("www.", "")
        slug = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
        return slug or "missing_url"

    def to_run_record(self) -> dict[str, Any]:
        status = self.status or "unknown"
        return {
            "run_id": f"static_page_{self.source_slug}_{_run_timestamp(self.started_at)}",
            "run_type": "sprint_10_static_page_source",
            "source_type": STATIC_PAGE_SOURCE,
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
            "config_searches_rows": 0,
            "companies_read": 1,
            "searches_read": 0,
            "error_message": self.error_message,
            "notes": f"Sprint 10 static page fetch for url={self.source_url}; http_status={self.http_status or ''}; low_confidence_count={self.low_confidence_count}",
            "created_at": self.finished_at,
            "updated_at": self.finished_at,
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "status": self.status,
            "records_found": self.records_found,
            "low_confidence_count": self.low_confidence_count,
            "error_message": self.error_message,
            "http_status": self.http_status,
        }


def fetch_static_page_html(
    source_url: str,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
) -> tuple[str, int | None]:
    if not source_url:
        raise ValueError("Static page source_url is required")
    client = session or requests
    response = client.get(
        source_url,
        timeout=timeout_seconds,
        headers={"User-Agent": "Mozilla/5.0 job-market-tracker static-page-check"},
    )
    response.raise_for_status()
    return response.text, getattr(response, "status_code", None)


def fetch_static_page_board(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    search_rows: list[dict[str, Any]] | None = None,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> StaticPageSourceResult:
    started_at = utc_now_iso()
    company_name = clean_text(company_row.get("company_name", ""))
    source_url = normalize_url(company_row.get("source_url", ""))
    if not source_url:
        finished_at = utc_now_iso()
        return StaticPageSourceResult(
            company_name=company_name,
            source_url="",
            status="failed",
            error_message="Missing static page source_url",
            started_at=started_at,
            finished_at=finished_at,
        )

    try:
        html, http_status = fetch_static_page_html(source_url, session=session, timeout_seconds=timeout_seconds)
        candidates = extract_static_page_candidates(html, source_url, company_row=company_row, search_rows=search_rows)
        company_context = extract_company_context(company_row)
        jobs: list[JobPosting] = []
        for candidate in candidates:
            job = candidate_to_job(candidate, company_row, seen_date=seen_date)
            if scoring_rules is not None:
                job = score_job(job, scoring_rules, company_context=company_context)
            job = mark_static_confidence(job, candidate)
            jobs.append(job)

        low_confidence_count = len([candidate for candidate in candidates if candidate.requires_manual_review])
        finished_at = utc_now_iso()
        return StaticPageSourceResult(
            company_name=company_name,
            source_url=source_url,
            status="success" if jobs else "empty",
            records_found=len(jobs),
            jobs=jobs,
            low_confidence_count=low_confidence_count,
            http_status=http_status,
            started_at=started_at,
            finished_at=finished_at,
        )
    except (requests.RequestException, ValueError) as exc:
        finished_at = utc_now_iso()
        return StaticPageSourceResult(
            company_name=company_name,
            source_url=source_url,
            status="failed",
            error_message=str(exc),
            started_at=started_at,
            finished_at=finished_at,
        )


def fetch_static_page_jobs(
    company_row: dict[str, Any],
    scoring_rules: dict[str, Any] | None = None,
    *,
    search_rows: list[dict[str, Any]] | None = None,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> list[JobPosting]:
    result = fetch_static_page_board(
        company_row,
        scoring_rules=scoring_rules,
        search_rows=search_rows,
        session=session,
        timeout_seconds=timeout_seconds,
        seen_date=seen_date,
    )
    return result.jobs


def append_static_page_run_results(sheet_client: Any, results: list[StaticPageSourceResult]) -> int:
    appended = 0
    for result in results:
        sheet_client.append_run(result.to_run_record())
        appended += 1
    return appended


def run_static_page_companies(
    company_rows: list[dict[str, Any]],
    scoring_rules: dict[str, Any] | None = None,
    *,
    search_rows: list[dict[str, Any]] | None = None,
    sheet_client: Any | None = None,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    seen_date: str | None = None,
) -> tuple[list[JobPosting], list[StaticPageSourceResult]]:
    jobs: list[JobPosting] = []
    results: list[StaticPageSourceResult] = []
    for company_row in static_page_company_rows(company_rows):
        result = fetch_static_page_board(
            company_row,
            scoring_rules=scoring_rules,
            search_rows=search_rows,
            session=session,
            timeout_seconds=timeout_seconds,
            seen_date=seen_date,
        )
        jobs.extend(result.jobs)
        results.append(result)
        if sheet_client is not None:
            sheet_client.append_run(result.to_run_record())
    return jobs, results
