from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

from src.models import JobPosting, utc_now_iso
from src.normalize import clean_text, normalize_url

REJECTED_JOBS_WORKSHEET = "Rejected_Jobs"
SPARSE_GMAIL_REVIEW_REASON = "sparse_gmail_high_signal_title"

JOB_BOARD_HOSTS = (
    "builtin.com",
    "builtinchicago.org",
    "builtindallas.com",
    "google.com",
    "indeed.com",
    "jobs.google.com",
    "ladders.com",
    "linkedin.com",
    "simplyhired.com",
    "theladders.com",
    "ziprecruiter.com",
)
TRACKING_OR_ASSET_HOSTS = (
    "licdn.com",
    "static.licdn.com",
)
STATIC_ASSET_EXTENSIONS = (
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".png",
    ".svg",
    ".webp",
)
GENERIC_TITLE_PATTERNS = (
    r"^job search(?:\s+search jobs)?\b",
    r"^search jobs\b",
    r"^jobs near me\b",
    r"\bjobs near me\b",
    r"^jobs in my city\b",
    r"^new jobs match your preferences\.?$",
    r"^your job alert for\b",
    r"^your job alert has been created\b",
    r"^job alert\b",
    r"^new jobs\b",
    r"^recommended jobs\b",
    r"^jobs you may be interested in\b",
)
ALERT_METADATA_PATTERNS = (
    r"\bjob alert\b",
    r"\bnew jobs match your preferences\b",
    r"\byour job alert for\b",
    r"\byour job alert has been created\b",
    r"\byou ll receive notifications when new jobs are posted that match your search preferences\b",
    r"\byou will receive notifications when new jobs are posted that match your search preferences\b",
    r"\bjobs near me\b",
    r"\bjobs in my city\b",
    r"\bsearch jobs\b",
)
ROLE_SIGNAL_KEYWORDS = (
    "analyst",
    "analytics",
    "business",
    "category",
    "chief of staff",
    "commercial",
    "consultant",
    "director",
    "finance",
    "fp&a",
    "general manager",
    "growth",
    "insights",
    "manager",
    "operations",
    "pricing",
    "product line",
    "program manager",
    "revenue",
    "sales operations",
    "senior",
    "strategy",
    "transformation",
    "vice president",
    "vp",
)
SEARCH_QUERY_KEYS = {
    "q",
    "query",
    "keyword",
    "keywords",
    "location",
    "locationId",
    "search",
    "searchTerm",
    "search_id",
}
NAVIGATION_PATH_PARTS = {
    "alert",
    "alerts",
    "apply",
    "browse",
    "categories",
    "category",
    "help",
    "home",
    "job-alert",
    "job-alerts",
    "job-search",
    "jobsearch",
    "jobs-near-me",
    "landing",
    "near-me",
    "profile",
    "profiles",
    "resume",
    "resumes",
    "search",
    "services",
    "sign-in",
    "signin",
    "top-companies",
}
GENERIC_JOB_PATHS = {
    "",
    "/",
    "/career",
    "/careers",
    "/employment",
    "/job",
    "/jobs",
    "/jobs/",
    "/openings",
    "/positions",
    "/search",
}
JOB_PATH_TERMS = {"job", "jobs", "opening", "openings", "position", "positions", "requisition", "careers", "career"}


@dataclass(frozen=True, slots=True)
class JobQualityRejection:
    job: JobPosting
    reason: str


def _identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def _stable_id(*parts: str, prefix: str = "rejected") -> str:
    digest = hashlib.sha1("|".join(part for part in parts if part).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _parts(url: str):
    try:
        return urlsplit(url)
    except ValueError:
        return urlsplit("")


def _path_parts(path: str) -> list[str]:
    return [part for part in re.split(r"[/-]+", path.lower()) if part]


def _slash_path_parts(path: str) -> list[str]:
    return [part for part in path.lower().split("/") if part]


def _query_keys(query: str) -> set[str]:
    keys: set[str] = set()
    for part in query.split("&"):
        if not part:
            continue
        keys.add(part.split("=", 1)[0])
    return keys


def is_generic_title(title: str) -> bool:
    text = _identity(title)
    if not text:
        return True
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in GENERIC_TITLE_PATTERNS)


def looks_like_alert_metadata(value: str) -> bool:
    text = _identity(value)
    return bool(text and any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in ALERT_METADATA_PATTERNS))


def title_has_role_signal(title: str) -> bool:
    text = _identity(title)
    return any(keyword in text for keyword in ROLE_SIGNAL_KEYWORDS)


def _is_job_board_host(host: str) -> bool:
    return any(_host_matches(host, suffix) for suffix in JOB_BOARD_HOSTS)


def _is_tracking_or_asset_host(host: str) -> bool:
    return any(_host_matches(host, suffix) for suffix in TRACKING_OR_ASSET_HOSTS)


def _is_linkedin_direct_job(path: str) -> bool:
    normalized = path.lower()
    normalized = normalized[5:] if normalized.startswith("/comm/") else normalized
    return bool(re.search(r"/jobs/view/\d+", normalized))


def _is_builtin_direct_job(path: str) -> bool:
    normalized = path.lower()
    return bool(re.search(r"/(?:job|jobs)/[^/]+/\d+", normalized) or re.search(r"/(?:job|jobs)/\d+", normalized))


def _is_lever_direct_job(host: str, path: str) -> bool:
    if not _host_matches(host, "jobs.lever.co"):
        return False
    slash_parts = _slash_path_parts(path)
    if len(slash_parts) < 2:
        return False
    return not (set(_path_parts(path)) & NAVIGATION_PATH_PARTS)


def _has_direct_posting_shape(path: str) -> bool:
    normalized = path.lower().rstrip("/")
    split_parts = _path_parts(normalized)
    slash_parts = _slash_path_parts(normalized)
    if not split_parts:
        return False
    if re.search(r"/(?:job|jobs|opening|openings|position|positions|requisition|careers|career)/\d+\b", normalized):
        return True
    if re.search(r"\d{4,}", normalized):
        return any(part in JOB_PATH_TERMS for part in split_parts)
    if len(slash_parts) >= 2 and any(part in JOB_PATH_TERMS for part in split_parts):
        last_part = slash_parts[-1]
        return len(last_part) >= 8 and last_part not in NAVIGATION_PATH_PARTS
    return False


def job_url_rejection_reason(url: str, source_primary: str = "") -> str:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return "missing_url"

    parts = _parts(normalized_url)
    host = parts.netloc.lower().replace("www.", "")
    path = parts.path.lower().rstrip("/") or "/"
    path_segments = set(_path_parts(path))
    query_keys = _query_keys(parts.query)

    if parts.scheme not in {"http", "https"} or not host:
        return "invalid_url"
    if _is_tracking_or_asset_host(host):
        return "tracking_or_static_asset_host"
    if any(path.endswith(extension) for extension in STATIC_ASSET_EXTENSIONS):
        return "static_asset_url"
    if _is_lever_direct_job(host, path):
        return ""
    if path in GENERIC_JOB_PATHS:
        return "generic_job_board_or_career_navigation_page"
    if path_segments & NAVIGATION_PATH_PARTS:
        if not (_host_matches(host, "linkedin.com") and _is_linkedin_direct_job(path)):
            return "search_category_landing_or_navigation_path"
    if query_keys & SEARCH_QUERY_KEYS:
        if not (_host_matches(host, "linkedin.com") and _is_linkedin_direct_job(path)):
            return "search_or_browse_query_url"

    if _host_matches(host, "linkedin.com"):
        return "" if _is_linkedin_direct_job(path) else "linkedin_not_direct_job_posting"

    if _is_job_board_host(host):
        if _host_matches(host, "builtin.com") or "builtin" in host:
            return "" if _is_builtin_direct_job(path) else "job_board_search_or_navigation_url"
        return "job_board_search_or_navigation_url"

    if _has_direct_posting_shape(path):
        return ""
    return "url_does_not_look_like_direct_job_posting"


def is_trusted_company_career_posting(url: str) -> bool:
    normalized_url = normalize_url(url)
    if not normalized_url:
        return False
    host = _parts(normalized_url).netloc.lower().replace("www.", "")
    return not _is_job_board_host(host) and not _is_tracking_or_asset_host(host) and job_url_rejection_reason(normalized_url, "static_page") == ""


def _manual_review_marker(job: JobPosting) -> bool:
    text = f"{job.description_text} {job.score_explanation}".lower()
    return "manual_review=true" in text


def _is_allowed_sparse_gmail_review(job: JobPosting, title: str, source: str, url_reason: str) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return (
        source == "gmail_alert"
        and f"review_reason={SPARSE_GMAIL_REVIEW_REASON}" in explanation
        and url_reason == ""
        and title_has_role_signal(title)
    )


def validate_job_quality(job: JobPosting) -> list[str]:
    reasons: list[str] = []
    title = clean_text(job.title)
    company = clean_text(job.company)
    source = clean_text(job.source_primary).lower()

    if not title:
        reasons.append("missing_title")
    elif is_generic_title(title):
        reasons.append("generic_alert_or_search_title")
    elif not title_has_role_signal(title):
        reasons.append("title_lacks_role_signal")

    if not company:
        reasons.append("missing_company")
    elif looks_like_alert_metadata(company):
        reasons.append("company_looks_like_alert_metadata")

    if title and company and _identity(title) == _identity(company):
        reasons.append("title_company_identical")

    if looks_like_alert_metadata(title):
        reasons.append("title_looks_like_alert_metadata")

    url_reason = job_url_rejection_reason(job.canonical_url, source)
    if url_reason:
        reasons.append(url_reason)

    if _manual_review_marker(job) and not _is_allowed_sparse_gmail_review(job, title, source, url_reason):
        if source != "static_page" or not is_trusted_company_career_posting(job.canonical_url) or not title_has_role_signal(title):
            reasons.append("manual_review_job_not_trusted_static_direct_posting")

    return _dedupe_reasons(reasons)


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            deduped.append(reason)
    return deduped


def filter_jobs_for_upsert(jobs: Iterable[JobPosting]) -> tuple[list[JobPosting], list[JobQualityRejection]]:
    accepted: list[JobPosting] = []
    rejected: list[JobQualityRejection] = []
    for job in jobs:
        reasons = validate_job_quality(job)
        if reasons:
            rejected.append(JobQualityRejection(job=job, reason=";".join(reasons)))
        else:
            accepted.append(job)
    return accepted, rejected


def rejected_job_record(rejection: JobQualityRejection) -> dict[str, Any]:
    job = rejection.job
    now = utc_now_iso()
    return {
        "rejected_id": _stable_id(job.source_primary, job.source_job_id, job.canonical_url, job.title, rejection.reason),
        "source": job.source_primary,
        "message_id": "",
        "thread_id": "",
        "subject": "",
        "sender": "",
        "received_date": job.last_seen_date or job.first_seen_date,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.canonical_url,
        "confidence": "rejected",
        "rejection_reason": rejection.reason,
        "extraction_notes": "final_data_quality_gate",
        "raw_evidence": clean_text(job.description_text)[:1000],
        "created_at": now,
        "updated_at": now,
    }


def append_rejected_jobs(sheet_client: Any, rejections: Iterable[JobQualityRejection]) -> int:
    rejected = list(rejections)
    if not rejected:
        return 0
    records = [rejected_job_record(rejection) for rejection in rejected]
    if hasattr(sheet_client, "ensure_worksheet"):
        sheet_client.ensure_worksheet(REJECTED_JOBS_WORKSHEET, rows=max(1000, len(records) + 10), cols=len(records[0]))
    if hasattr(sheet_client, "append_records"):
        sheet_client.append_records(REJECTED_JOBS_WORKSHEET, records)
    else:
        for record in records:
            sheet_client.append_record(REJECTED_JOBS_WORKSHEET, record)
    return len(records)
