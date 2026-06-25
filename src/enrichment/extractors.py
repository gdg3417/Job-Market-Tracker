from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.enrichment.fetcher import FetchResult
from src.enrichment.json_ld import best_job_posting
from src.enrichment.models import EnrichmentEvidence, utc_now_iso

GENERIC_PAGE_TITLES = {
    "careers",
    "career opportunities",
    "jobs",
    "job search",
    "search jobs",
    "open positions",
    "join our team",
}
GENERIC_SITE_NAMES = {"linkedin", "indeed", "glassdoor", "ziprecruiter"}
JOB_MARKERS = (
    "responsibilities",
    "qualifications",
    "requirements",
    "job description",
    "about the role",
    "what you'll do",
    "what you will do",
    "apply now",
)
TEAM_TERMS = (
    "direct reports",
    "people manager",
    "manage a team",
    "manages a team",
    "lead a team",
    "leads a team",
    "team leadership",
    "supervise",
    "reports to",
    "reporting to",
)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _meta(soup: BeautifulSoup, *keys: str) -> str:
    lowered = {key.lower() for key in keys}
    for tag in soup.find_all("meta"):
        name = str(tag.get("name") or tag.get("property") or tag.get("itemprop") or "").strip().lower()
        if name in lowered:
            content = _clean_text(tag.get("content"))
            if content:
                return content
    return ""


def _canonical_url(soup: BeautifulSoup, base_url: str) -> str:
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rel_values = {str(value).strip().lower() for value in (rel if isinstance(rel, list) else [rel])}
        if "canonical" in rel_values:
            href = _clean_text(link.get("href"))
            if href:
                return urljoin(base_url, href)
    return base_url


def _page_title(soup: BeautifulSoup) -> str:
    title = _meta(soup, "job:title", "twitter:title", "og:title")
    if title:
        return title
    heading = soup.find("h1")
    if heading is not None:
        return _clean_text(heading.get_text(" ", strip=True))
    return _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")


def _linkedin_title_fields(value: str) -> tuple[str, str, str]:
    title = _clean_text(value)
    suffix_match = re.match(r"^(?P<body>.+?)\s*\|\s*LinkedIn$", title, flags=re.IGNORECASE)
    if suffix_match is None:
        return "", "", ""
    body = suffix_match.group("body").strip()
    hiring_marker = re.search(r"\s+hiring\s+", body, flags=re.IGNORECASE)
    if hiring_marker is None:
        return "", "", ""
    company = body[: hiring_marker.start()].strip()
    remainder = body[hiring_marker.end() :].strip()
    location = ""
    location_match = re.match(r"^(?P<title>.+)\s+in\s+(?P<location>[^|]+)$", remainder, flags=re.IGNORECASE)
    if location_match is not None:
        role_title = location_match.group("title").strip()
        location = location_match.group("location").strip()
    else:
        role_title = remainder
    return _clean_text(company), _clean_text(role_title), _clean_text(location)


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    return _clean_text(soup.get_text(" ", strip=True))


def _looks_like_job_page(title: str, text: str) -> bool:
    normalized_title = _clean_text(title).lower()
    if normalized_title in GENERIC_PAGE_TITLES or len(normalized_title) < 4:
        return False
    lowered = text.lower()
    marker_count = sum(marker in lowered for marker in JOB_MARKERS)
    word_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9&'/-]*", text))
    return marker_count >= 2 and word_count >= 80


def _team_leadership_text(description: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", description)
    matches = [sentence.strip() for sentence in sentences if any(term in sentence.lower() for term in TEAM_TERMS)]
    return " ".join(matches[:5])[:1500]


def _metadata_candidate(soup: BeautifulSoup, final_url: str) -> dict[str, Any] | None:
    page_title = _page_title(soup)
    text = _visible_text(soup)
    if not _looks_like_job_page(page_title, text):
        return None

    linkedin_company, linkedin_title, linkedin_location = _linkedin_title_fields(page_title)
    title = linkedin_title or page_title
    company = _meta(soup, "hiringorganization", "job:company", "og:site_name", "application-name")
    if company.lower() in GENERIC_SITE_NAMES:
        company = ""
    company = company or linkedin_company
    location = _meta(soup, "job:location", "joblocation", "location") or linkedin_location

    description = _meta(soup, "description", "og:description")
    if len(description.split()) < 40:
        description = text
    lowered = description.lower()
    if "hybrid" in lowered:
        remote_status = "hybrid"
        work_model = "hybrid"
    elif re.search(r"\bremote\b", lowered):
        remote_status = "remote"
        work_model = "remote"
    elif location:
        remote_status = "on-site"
        work_model = "on-site"
    else:
        remote_status = "unknown"
        work_model = "unknown"
    return {
        "source_title": title,
        "source_company": company,
        "source_location": location,
        "description_text": description,
        "salary_min": None,
        "salary_max": None,
        "currency": "",
        "employment_type": _meta(soup, "job:employmenttype", "employmenttype"),
        "remote_status": remote_status,
        "work_model": work_model,
        "posting_date": _meta(soup, "dateposted", "job:dateposted")[:10],
        "valid_through": _meta(soup, "validthrough", "job:validthrough")[:10],
        "canonical_url": _canonical_url(soup, final_url),
    }


def extract_job_evidence(
    fetch_result: FetchResult,
    *,
    job_key: str,
    enrichment_id: str,
    retrieved_at: str | None = None,
) -> EnrichmentEvidence | None:
    html = fetch_result.text or ""
    structured = best_job_posting(html)
    soup = BeautifulSoup(html, "html.parser")
    candidate = structured or _metadata_candidate(soup, fetch_result.final_url)
    if candidate is None:
        return None

    canonical_url = _clean_text(candidate.get("canonical_url"))
    if canonical_url:
        canonical_url = urljoin(fetch_result.final_url, canonical_url)
    else:
        canonical_url = _canonical_url(soup, fetch_result.final_url)

    description = _clean_text(candidate.get("description_text"))
    evidence = EnrichmentEvidence(
        job_key=job_key,
        enrichment_id=enrichment_id,
        source_type="direct_url_json_ld" if structured else "direct_url_metadata",
        source_url=fetch_result.final_url,
        retrieved_at=retrieved_at or utc_now_iso(),
        http_status=fetch_result.status_code,
        canonical_url=canonical_url,
        source_title=_clean_text(candidate.get("source_title")),
        source_company=_clean_text(candidate.get("source_company")),
        source_location=_clean_text(candidate.get("source_location")),
        description_text=description,
        salary_min=candidate.get("salary_min"),
        salary_max=candidate.get("salary_max"),
        currency=_clean_text(candidate.get("currency")),
        employment_type=_clean_text(candidate.get("employment_type")),
        remote_status=_clean_text(candidate.get("remote_status")) or "unknown",
        work_model=_clean_text(candidate.get("work_model")) or "unknown",
        posting_date=_clean_text(candidate.get("posting_date")),
        valid_through=_clean_text(candidate.get("valid_through")),
        team_leadership_text=_team_leadership_text(description),
        raw_content_hash=hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest(),
    )
    return replace(evidence, canonical_url=canonical_url or fetch_result.final_url)
