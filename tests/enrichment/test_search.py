from __future__ import annotations

from src.enrichment.company_config import company_config_from_row
from src.enrichment.search import (
    SearchCacheRecord,
    build_search_plan,
    is_authoritative_candidate,
    is_denied_automatic_candidate,
    parse_duckduckgo_results,
    query_id_for,
)
from src.models import JobPosting


def test_search_plan_prioritizes_official_company_domain_and_exposes_manual_links():
    job = JobPosting(company="Topgolf", title="Sr Manager, Strategic Planning", location="Dallas, TX")
    config = company_config_from_row(
        {
            "company_name": "Topgolf",
            "canonical_company_name": "Topgolf",
            "career_domain": "careers.topgolf.com",
            "career_search_url": "https://careers.topgolf.com/us/search-results",
            "enrichment_active": True,
        }
    )

    plan = build_search_plan(job, config)

    assert plan.queries[0].startswith("site:careers.topgolf.com")
    assert '"Sr Manager, Strategic Planning"' in plan.queries[0]
    assert len(plan.queries) >= 3
    assert "Senior Manager Strategic Planning" in plan.queries[1]
    assert plan.preferred_manual_url == "https://careers.topgolf.com/us/search-results"
    labels = [label for label, _ in plan.manual_links]
    assert labels == ["Company careers", "Google", "Bing", "DuckDuckGo", "LinkedIn", "Indeed"]


def test_search_plan_uses_configured_canonical_company_for_broad_fallback():
    job = JobPosting(
        company="Toyota North America",
        title="National Mgr, Product",
        location="Plano, TX",
    )
    config = company_config_from_row(
        {
            "company_name": "Toyota Motor North America",
            "canonical_company_name": "Toyota Motor North America",
            "company_aliases": "Toyota North America",
            "career_domain": "careers.toyota.com",
            "career_search_url": "https://careers.toyota.com/us/search-results",
            "enrichment_active": True,
        }
    )

    plan = build_search_plan(job, config)

    assert "Manager" in plan.queries[1]
    assert any('"Toyota Motor North America"' in query for query in plan.queries)


def test_duckduckgo_parser_decodes_redirect_and_deduplicates_results():
    target = "https%3A%2F%2Fcareers.example.com%2Fjobs%2F123"
    html = f"""
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg={target}">Director, Commercial Strategy</a>
      <div class="result__snippet">Lead strategy and growth.</div>
    </div>
    <div class="result">
      <a class="result__a" href="https://careers.example.com/jobs/123">Duplicate</a>
    </div>
    """

    rows = parse_duckduckgo_results(html, query="example", provider="duckduckgo_html", limit=5)

    assert len(rows) == 1
    assert rows[0].url == "https://careers.example.com/jobs/123"
    assert rows[0].snippet == "Lead strategy and growth."


def test_automatic_candidates_require_company_or_supported_ats_authority():
    config = company_config_from_row(
        {
            "company_name": "Example Company",
            "canonical_company_name": "Example Company",
            "career_domain": "careers.example.com",
            "career_search_url": "https://careers.example.com/search",
            "enrichment_active": True,
        }
    )

    assert is_authoritative_candidate("https://careers.example.com/jobs/123", config)
    assert is_authoritative_candidate("https://jobs.lever.co/example/123", config)
    assert not is_authoritative_candidate("https://www.linkedin.com/jobs/view/123", config)
    assert not is_authoritative_candidate("https://unknown.example.net/jobs/123", config)
    assert is_denied_automatic_candidate("https://www.indeed.com/viewjob?jk=123")


def test_search_cache_record_has_stable_query_id_and_24_hour_freshness():
    first = query_id_for("duckduckgo_html", '"Example" "Director Strategy"')
    second = query_id_for("DUCKDUCKGO_HTML", '  "Example"   "Director Strategy"  ')
    record = SearchCacheRecord(
        query_id=first,
        searched_at="2026-06-24T12:00:00Z",
        result_urls="https://careers.example.com/jobs/1|https://careers.example.com/jobs/1",
    )

    assert first == second
    assert record.urls == ["https://careers.example.com/jobs/1"]
    assert record.is_fresh(now="2026-06-25T11:59:59Z")
    assert not record.is_fresh(now="2026-06-25T12:00:01Z")


def test_failed_search_response_is_not_reused_as_fresh_cache():
    record = SearchCacheRecord(
        status="failed",
        searched_at="2026-06-25T11:00:00Z",
    )

    assert not record.is_fresh(now="2026-06-25T11:30:00Z")


def test_unconfigured_company_domain_requires_company_identity_and_career_marker():
    assert is_authoritative_candidate(
        "https://careers.acmeindustrial.com/jobs/123",
        None,
        company="Acme Industrial",
    )
    assert is_authoritative_candidate(
        "https://www.acmeindustrial.com/careers/positions/123",
        None,
        company="Acme Industrial",
    )
    assert is_authoritative_candidate(
        "https://careers.time.com/jobs/123",
        None,
        company="Time Manufacturing",
    )
    assert not is_authoritative_candidate(
        "https://news.acmeindustrial.com/articles/123",
        None,
        company="Acme Industrial",
    )
    assert not is_authoritative_candidate(
        "https://careers.unrelated.com/jobs/123",
        None,
        company="Acme Industrial",
    )
    assert not is_authoritative_candidate(
        "https://careers.sometimes.com/jobs/123",
        None,
        company="Time Manufacturing",
    )
    assert not is_authoritative_candidate(
        "https://time.jobs.com/jobs/123",
        None,
        company="Time Manufacturing",
    )
