from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import requests
from bs4 import BeautifulSoup

from src.enrichment.company_config import CompanyEnrichmentConfig
from src.enrichment.fetcher import is_safe_public_url
from src.models import JobPosting

DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DEFAULT_SEARCH_USER_AGENT = "JobMarketTracker-Enrichment/1.0 (+external-search-stage)"
DEFAULT_CACHE_TTL = timedelta(hours=24)
DEFAULT_QUERY_BUDGET = 3
DEFAULT_RESULTS_PER_QUERY = 5
DEFAULT_CANDIDATE_PAGE_BUDGET = 5

SEARCH_ENGINE_HOSTS = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
}
JOB_BOARD_HOST_SUFFIXES = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "careerbuilder.com",
    "simplyhired.com",
    "theladders.com",
)

GENERIC_COMPANY_DOMAIN_TERMS = {
    "and",
    "company",
    "corporation",
    "corp",
    "global",
    "group",
    "holdings",
    "inc",
    "international",
    "llc",
    "north",
    "america",
    "the",
}
CAREER_URL_MARKERS = ("career", "careers", "job", "jobs", "position", "positions", "opening", "openings")

AUTHORITATIVE_ATS_HOST_SUFFIXES = (
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "myworkdayjobs.com",
    "icims.com",
    "successfactors.com",
    "phenompeople.com",
    "oraclecloud.com",
)


class ResponseLike(Protocol):
    status_code: int
    text: str

    def raise_for_status(self) -> None:
        ...


class SessionLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> ResponseLike:
        ...


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int) -> "SearchResponse":
        ...


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""
    provider: str = ""


@dataclass(slots=True)
class SearchResponse:
    provider: str
    query: str
    search_url: str
    status: str
    candidates: list[SearchCandidate] = field(default_factory=list)
    error_message: str = ""
    http_status: int | None = None
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class SearchPlan:
    queries: tuple[str, ...]
    manual_links: tuple[tuple[str, str], ...]

    @property
    def preferred_manual_url(self) -> str:
        return self.manual_links[0][1] if self.manual_links else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": list(self.queries),
            "manual_links": [{"label": label, "url": url} for label, url in self.manual_links],
            "preferred_manual_url": self.preferred_manual_url,
        }


@dataclass(slots=True)
class SearchCacheRecord:
    query_id: str = ""
    job_key: str = ""
    enrichment_id: str = ""
    provider: str = "duckduckgo_html"
    query_text: str = ""
    search_url: str = ""
    searched_at: str = ""
    status: str = ""
    result_urls: str = ""
    error_message: str = ""

    @property
    def urls(self) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for value in re.split(r"[|\n]+", self.result_urls):
            url = normalize_candidate_url(value)
            if url and url not in seen:
                urls.append(url)
                seen.add(url)
        return urls

    def is_fresh(self, *, now: str, ttl: timedelta = DEFAULT_CACHE_TTL) -> bool:
        searched = parse_timestamp(self.searched_at)
        current = parse_timestamp(now)
        return searched is not None and current is not None and timedelta(0) <= current - searched <= ttl


class DuckDuckGoHtmlSearchProvider:
    name = "duckduckgo_html"

    def __init__(self, *, session: SessionLike | None = None, timeout_seconds: int = 15) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = max(1, int(timeout_seconds))

    def search(self, query: str, *, limit: int = DEFAULT_RESULTS_PER_QUERY) -> SearchResponse:
        clean_query = clean_text(query)
        search_url = duckduckgo_search_url(clean_query)
        if not clean_query or limit <= 0:
            return SearchResponse(self.name, clean_query, search_url, "empty")
        try:
            response = self.session.get(
                DUCKDUCKGO_HTML_URL,
                params={"q": clean_query},
                timeout=self.timeout_seconds,
                headers={"User-Agent": DEFAULT_SEARCH_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            )
            response.raise_for_status()
            candidates = parse_duckduckgo_results(response.text, query=clean_query, provider=self.name, limit=limit)
            return SearchResponse(
                provider=self.name,
                query=clean_query,
                search_url=search_url,
                status="success" if candidates else "empty",
                candidates=candidates,
                http_status=getattr(response, "status_code", None),
            )
        except requests.RequestException as exc:
            return SearchResponse(self.name, clean_query, search_url, "failed", error_message=str(exc))


class DisabledSearchProvider:
    name = "manual_only"

    def search(self, query: str, *, limit: int = DEFAULT_RESULTS_PER_QUERY) -> SearchResponse:
        del limit
        clean_query = clean_text(query)
        return SearchResponse(self.name, clean_query, duckduckgo_search_url(clean_query), "disabled")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def query_id_for(provider: str, query: str) -> str:
    material = f"{clean_text(provider).lower()}|{clean_text(query).lower()}"
    return f"qry_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def encode_result_urls(urls: Iterable[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in urls:
        url = normalize_candidate_url(value)
        if url and url not in seen:
            unique.append(url)
            seen.add(url)
    return "|".join(unique)


def _hostname(url: str) -> str:
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _host_matches(host: str, suffix: str) -> bool:
    suffix = suffix.rstrip(".").lower()
    return host == suffix or host.endswith(f".{suffix}")


def normalize_candidate_url(value: Any) -> str:
    url = clean_text(value)
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if _host_matches(host, "duckduckgo.com") and parts.path.startswith("/l/"):
        redirected = parse_qs(parts.query).get("uddg", [""])[0]
        if redirected:
            url = unquote(redirected)
    return url if is_safe_public_url(url) else ""


def is_denied_automatic_candidate(url: str) -> bool:
    host = _hostname(url)
    if not host:
        return True
    if host in SEARCH_ENGINE_HOSTS:
        return True
    return any(_host_matches(host, suffix) for suffix in JOB_BOARD_HOST_SUFFIXES)


def _company_domain_candidate(url: str, company: str) -> bool:
    normalized = normalize_candidate_url(url)
    if not normalized:
        return False
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    host_compact = re.sub(r"[^a-z0-9]+", "", host)
    company_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", clean_text(company).lower())
        if len(token) >= 4 and token not in GENERIC_COMPANY_DOMAIN_TERMS
    ]
    if not company_tokens or not any(token in host_compact for token in company_tokens):
        return False
    career_text = f"{host} {parts.path}".lower()
    return any(marker in career_text for marker in CAREER_URL_MARKERS)


def is_authoritative_candidate(
    url: str,
    config: CompanyEnrichmentConfig | None = None,
    *,
    company: str = "",
) -> bool:
    normalized = normalize_candidate_url(url)
    host = _hostname(normalized)
    if not normalized or not host or is_denied_automatic_candidate(normalized):
        return False
    career_domain = clean_text(config.career_domain).lower() if config else ""
    if career_domain and _host_matches(host, career_domain):
        return True
    configured_hosts = {
        _hostname(config.career_search_url) if config else "",
        _hostname(config.source_url) if config else "",
    }
    if host in configured_hosts - {""}:
        return True
    if any(_host_matches(host, suffix) for suffix in AUTHORITATIVE_ATS_HOST_SUFFIXES):
        return True
    return not (career_domain or configured_hosts - {""}) and _company_domain_candidate(normalized, company)


def candidate_authority_rank(
    url: str,
    config: CompanyEnrichmentConfig | None = None,
    *,
    company: str = "",
) -> int:
    host = _hostname(url)
    career_domain = clean_text(config.career_domain).lower() if config else ""
    if career_domain and _host_matches(host, career_domain):
        return 0
    configured_hosts = {
        _hostname(config.career_search_url) if config else "",
        _hostname(config.source_url) if config else "",
    }
    if host in configured_hosts - {""}:
        return 1
    if any(_host_matches(host, suffix) for suffix in AUTHORITATIVE_ATS_HOST_SUFFIXES):
        return 2
    if not (career_domain or configured_hosts - {""}) and _company_domain_candidate(url, company):
        return 3
    return 9


def _quoted(value: str) -> str:
    text = clean_text(value).replace('"', "")
    return f'"{text}"' if text else ""


def _query(*parts: str) -> str:
    return clean_text(" ".join(part for part in parts if clean_text(part)))


def google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(clean_text(query))}"


def bing_search_url(query: str) -> str:
    return f"https://www.bing.com/search?q={quote_plus(clean_text(query))}"


def duckduckgo_search_url(query: str) -> str:
    return f"https://duckduckgo.com/?q={quote_plus(clean_text(query))}"


def linkedin_search_url(job: JobPosting) -> str:
    keywords = clean_text(f"{job.company} {job.title}")
    location = clean_text(job.location)
    url = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(keywords)}"
    return f"{url}&location={quote_plus(location)}" if location else url


def indeed_search_url(job: JobPosting) -> str:
    url = f"https://www.indeed.com/jobs?q={quote_plus(clean_text(f'{job.company} {job.title}'))}"
    location = clean_text(job.location)
    return f"{url}&l={quote_plus(location)}" if location else url


def build_search_plan(job: JobPosting, config: CompanyEnrichmentConfig | None = None) -> SearchPlan:
    title = _quoted(job.title)
    company = _quoted(job.company)
    location = _quoted(job.location)
    queries: list[str] = []

    career_domain = clean_text(config.career_domain).lower() if config else ""
    if career_domain:
        queries.append(_query(f"site:{career_domain}", title, company, location))

    configured_host = _hostname(config.career_search_url or config.source_url) if config else ""
    if configured_host and configured_host != career_domain:
        queries.append(_query(f"site:{configured_host}", title, company, location))

    queries.append(_query(company, title, location, "jobs"))

    unique_queries: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if query and key not in seen:
            unique_queries.append(query)
            seen.add(key)

    primary_query = unique_queries[0] if unique_queries else _query(company, title, "jobs")
    manual_links: list[tuple[str, str]] = []
    if config and clean_text(config.career_search_url):
        manual_links.append(("Company careers", clean_text(config.career_search_url)))
    manual_links.extend(
        [
            ("Google", google_search_url(primary_query)),
            ("Bing", bing_search_url(primary_query)),
            ("DuckDuckGo", duckduckgo_search_url(primary_query)),
            ("LinkedIn", linkedin_search_url(job)),
            ("Indeed", indeed_search_url(job)),
        ]
    )
    deduped_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for label, url in manual_links:
        if url and url not in seen_urls:
            deduped_links.append((label, url))
            seen_urls.add(url)
    return SearchPlan(tuple(unique_queries), tuple(deduped_links))


def parse_duckduckgo_results(html: str, *, query: str, provider: str, limit: int) -> list[SearchCandidate]:
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for result in soup.select(".result"):
        anchor = result.select_one("a.result__a")
        if anchor is None:
            continue
        url = normalize_candidate_url(anchor.get("href"))
        if not url or url in seen:
            continue
        snippet_node = result.select_one(".result__snippet")
        candidates.append(
            SearchCandidate(
                url=url,
                title=clean_text(anchor.get_text(" ", strip=True)),
                snippet=clean_text(snippet_node.get_text(" ", strip=True) if snippet_node else ""),
                query=clean_text(query),
                provider=clean_text(provider),
            )
        )
        seen.add(url)
        if len(candidates) >= max(0, limit):
            break
    return candidates
