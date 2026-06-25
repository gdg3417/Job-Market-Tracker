from __future__ import annotations

from src.enrichment.company_config import company_config_from_row
from src.enrichment.fetcher import FetchResult
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem
from src.enrichment.search import SearchCandidate, SearchResponse
from src.enrichment.search_run import run_external_search_enrichment
from src.models import JobPosting

NOW = "2026-06-24T12:00:00Z"


class FakeSheetClient:
    def __init__(self, jobs, queue_items):
        self.tables = {
            "Jobs": [job.to_dict() for job in jobs],
            "Enrichment_Queue": [item.to_dict() for item in queue_items],
            "Enrichment_Evidence": [],
            "Config_Companies": [],
        }

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def read_jobs_with_row_numbers(self):
        return [(index + 2, JobPosting.from_dict(row)) for index, row in enumerate(self.tables["Jobs"])]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()


class FakeProvider:
    name = "duckduckgo_html"

    def __init__(self, urls):
        self.urls = list(urls)
        self.calls = []

    def search(self, query, *, limit):
        self.calls.append((query, limit))
        return SearchResponse(
            provider=self.name,
            query=query,
            search_url="https://duckduckgo.com/?q=example",
            status="success" if self.urls else "empty",
            candidates=[SearchCandidate(url=url, query=query, provider=self.name) for url in self.urls[:limit]],
        )


class FakeFetcher:
    def __init__(self, final_urls=None):
        self.urls = []
        self.final_urls = dict(final_urls or {})

    def fetch(self, url):
        self.urls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=self.final_urls.get(url, url),
            status_code=200,
            content_type="text/html",
            text="<html><h1>Job</h1></html>",
        )


def job_and_queue(job_key="job-1", company="Example Company", title="Sr Manager, Strategic Planning"):
    job = JobPosting(
        job_key=job_key,
        company=company,
        title=title,
        location="Dallas, TX",
        canonical_url=f"https://alerts.example/{job_key}",
        source_job_id=job_key,
        source_primary="gmail_alert",
        description_text="Extracted from Gmail alert",
        status="open",
        potential_priority_score=85,
        potential_priority="high",
        score_status="provisional",
        enrichment_status="not_found",
        enrichment_priority="high",
        score_explanation="manual_review=true",
    )
    queue = EnrichmentQueueItem(
        enrichment_id=f"enr-{job_key}",
        job_key=job_key,
        company=company,
        title=title,
        location="Dallas, TX",
        lead_url=job.canonical_url,
        priority="high",
        status="not_found",
        current_stage="company_ats",
        attempt_count=2,
        created_at=NOW,
        updated_at=NOW,
    )
    return job, queue


def config(company="Example Company"):
    return company_config_from_row(
        {
            "company_name": company,
            "canonical_company_name": company,
            "career_domain": "careers.example.com",
            "career_search_url": "https://careers.example.com/search",
            "enrichment_active": True,
        }
    )


def evidence_for(job, url, *, title=None, company=None):
    return EnrichmentEvidence(
        job_key=job.job_key,
        enrichment_id=f"enr-{job.job_key}",
        source_type="direct_url_json_ld",
        source_url=url,
        canonical_url=url,
        source_title=title or job.title,
        source_company=company or job.company,
        source_location=job.location,
        description_text="Responsibilities include strategic planning, growth ownership, executive partnership, and team leadership. Qualifications include eight years of experience.",
        raw_content_hash=f"hash-{url}",
    )


def test_unique_authoritative_80_plus_match_is_merged(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    provider = FakeProvider(["https://careers.example.com/jobs/123"])
    fetcher = FakeFetcher()
    monkeypatch.setattr(
        "src.enrichment.search_run.extract_job_evidence",
        lambda fetched, **_kwargs: evidence_for(job, fetched.final_url),
    )

    summary = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
    )

    assert summary.enriched == 1
    assert len(client.tables["Jobs"]) == 1
    assert client.tables["Jobs"][0]["canonical_url"] == "https://careers.example.com/jobs/123"
    assert client.tables["Enrichment_Queue"][0]["current_stage"] == "external_search"
    assert client.tables["Enrichment_Queue"][0]["match_confidence"] >= 80
    assert any(row["source_type"] == "external_search_discovery" and row["accepted"] is False for row in client.tables["Enrichment_Evidence"])
    assert any(row["source_type"] == "external_search_page" and row["accepted"] is True for row in client.tables["Enrichment_Evidence"])


def test_multiple_plausible_candidates_are_ambiguous_and_not_merged(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    urls = ["https://careers.example.com/jobs/123", "https://careers.example.com/jobs/456"]
    provider = FakeProvider(urls)
    monkeypatch.setattr(
        "src.enrichment.search_run.extract_job_evidence",
        lambda fetched, **_kwargs: evidence_for(job, fetched.final_url),
    )

    summary = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=FakeFetcher(),
        priority_rules={},
        now=NOW,
    )

    assert summary.ambiguous == 1
    assert client.tables["Jobs"][0]["canonical_url"].startswith("https://alerts.example/")
    assert client.tables["Jobs"][0]["enrichment_status"] == "ambiguous"
    assert "manual_review_url=https://careers.example.com/jobs/" in client.tables["Jobs"][0]["score_explanation"]
    page_rows = [row for row in client.tables["Enrichment_Evidence"] if row["source_type"] == "external_search_page"]
    assert len(page_rows) == 2
    assert all(row["accepted"] is False for row in page_rows)


def test_job_board_search_result_is_discovery_only_and_manual_link_is_exposed(monkeypatch):
    job, queue = job_and_queue(company="Topgolf")
    client = FakeSheetClient([job], [queue])
    provider = FakeProvider(["https://www.linkedin.com/jobs/view/123"])
    fetcher = FakeFetcher()
    monkeypatch.setattr("src.enrichment.search_run.extract_job_evidence", lambda *_args, **_kwargs: None)
    topgolf = company_config_from_row(
        {
            "company_name": "Topgolf",
            "canonical_company_name": "Topgolf",
            "career_domain": "careers.topgolf.com",
            "career_search_url": "https://careers.topgolf.com/us/search-results",
            "enrichment_active": True,
        }
    )

    summary = run_external_search_enrichment(
        client,
        configs=[topgolf],
        provider=provider,
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
    )

    assert summary.not_found == 1
    assert summary.candidates_filtered == 1
    assert fetcher.urls == []
    row = client.tables["Jobs"][0]
    assert row["enrichment_source_url"] == "https://careers.topgolf.com/us/search-results"
    assert "manual_review_url=https://careers.topgolf.com/us/search-results" in row["score_explanation"]
    discovery = [item for item in client.tables["Enrichment_Evidence"] if item["source_type"] == "external_search_discovery"]
    assert discovery and all(item["accepted"] is False for item in discovery)


def test_fresh_discovery_cache_skips_repeat_query_and_uses_remaining_budget(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    provider = FakeProvider([])
    monkeypatch.setattr("src.enrichment.search_run.extract_job_evidence", lambda *_args, **_kwargs: None)

    first = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=FakeFetcher(),
        priority_rules={},
        now=NOW,
        query_budget=1,
    )
    client.tables["Enrichment_Queue"][0]["current_stage"] = "company_ats"
    client.tables["Enrichment_Queue"][0]["status"] = "not_found"
    client.tables["Jobs"][0]["enrichment_status"] = "not_found"
    second = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=FakeFetcher(),
        priority_rules={},
        now="2026-06-24T13:00:00Z",
        query_budget=1,
    )

    assert first.queries_executed == 1
    assert second.queries_executed == 1
    assert second.cache_hits == 1
    assert len(provider.calls) == 2
    assert provider.calls[0][0] != provider.calls[1][0]


def test_manual_candidate_url_requires_job_key_and_still_runs_full_validation(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    fetcher = FakeFetcher()
    monkeypatch.setattr(
        "src.enrichment.search_run.extract_job_evidence",
        lambda fetched, **_kwargs: evidence_for(job, fetched.final_url),
    )

    try:
        run_external_search_enrichment(
            client,
            configs=[config()],
            provider=FakeProvider([]),
            fetcher=fetcher,
            priority_rules={},
            now=NOW,
            candidate_urls=["https://careers.example.com/jobs/123"],
        )
    except ValueError as exc:
        assert "job_key" in str(exc)
    else:
        raise AssertionError("Expected ValueError")

    client.tables["Enrichment_Queue"][0]["current_stage"] = "external_search"
    client.tables["Enrichment_Queue"][0]["status"] = "not_found"
    client.tables["Jobs"][0]["enrichment_status"] = "not_found"

    summary = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=FakeProvider([]),
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
        job_key=job.job_key,
        candidate_urls=["https://careers.example.com/jobs/123"],
    )
    assert summary.enriched == 1
    assert fetcher.urls == ["https://careers.example.com/jobs/123"]


def test_authoritative_candidate_redirecting_to_job_board_is_rejected(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    source = "https://careers.example.com/jobs/123"
    provider = FakeProvider([source])
    fetcher = FakeFetcher({source: "https://www.linkedin.com/jobs/view/123"})
    monkeypatch.setattr(
        "src.enrichment.search_run.extract_job_evidence",
        lambda fetched, **_kwargs: evidence_for(job, fetched.final_url),
    )

    summary = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
    )

    assert summary.not_found == 1
    assert summary.candidate_pages_rejected == 1
    failures = [row for row in client.tables["Enrichment_Evidence"] if row["source_type"] == "external_search_candidate_failure"]
    assert failures and "non_authoritative_redirect" in failures[0]["rejection_reason"]


def test_duplicate_urls_with_same_canonical_posting_do_not_create_false_ambiguity(monkeypatch):
    job, queue = job_and_queue()
    client = FakeSheetClient([job], [queue])
    urls = [
        "https://careers.example.com/jobs/123?source=a",
        "https://careers.example.com/jobs/123?source=b",
    ]
    provider = FakeProvider(urls)

    def extracted(fetched, **_kwargs):
        evidence = evidence_for(job, fetched.final_url)
        evidence.canonical_url = "https://careers.example.com/jobs/123"
        return evidence

    monkeypatch.setattr("src.enrichment.search_run.extract_job_evidence", extracted)

    summary = run_external_search_enrichment(
        client,
        configs=[config()],
        provider=provider,
        fetcher=FakeFetcher(),
        priority_rules={},
        now=NOW,
    )

    assert summary.enriched == 1
    assert summary.ambiguous == 0
    assert client.tables["Jobs"][0]["canonical_url"] == "https://careers.example.com/jobs/123"
