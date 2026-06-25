from __future__ import annotations

import json

import pytest

from src.enrichment.company_config import company_config_from_row
from src.enrichment.fetcher import FetchResult
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem
from src.enrichment.search import SearchCandidate, SearchResponse
from src.enrichment.search_run import run_external_search_enrichment
from src.models import JobPosting

NOW = "2026-06-25T12:00:00Z"


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


class OrderedProvider:
    name = "duckduckgo_html"

    def __init__(self, urls):
        self.urls = list(urls)

    def search(self, query, *, limit):
        return SearchResponse(
            provider=self.name,
            query=query,
            search_url="https://duckduckgo.com/?q=example",
            status="success",
            candidates=[SearchCandidate(url=url, query=query, provider=self.name) for url in self.urls[:limit]],
        )


class MappingFetcher:
    def __init__(self, html_by_url=None):
        self.html_by_url = dict(html_by_url or {})
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text=self.html_by_url.get(url, "<html><h1>Job</h1></html>"),
        )


def make_job_and_queue(job_key="job-review"):
    job = JobPosting(
        job_key=job_key,
        company="Example Company",
        title="Sr Manager, Strategic Planning",
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
    )
    queue = EnrichmentQueueItem(
        enrichment_id=f"enr-{job_key}",
        job_key=job_key,
        company=job.company,
        title=job.title,
        location=job.location,
        lead_url=job.canonical_url,
        priority="high",
        status="not_found",
        current_stage="company_ats",
        attempt_count=2,
        created_at=NOW,
        updated_at=NOW,
    )
    return job, queue


def company_config():
    return company_config_from_row(
        {
            "company_name": "Example Company",
            "canonical_company_name": "Example Company",
            "career_domain": "careers.example.com",
            "career_search_url": "https://careers.example.com/search",
            "enrichment_active": True,
        }
    )


def evidence_for(job, url, *, correct):
    return EnrichmentEvidence(
        job_key=job.job_key,
        enrichment_id=f"enr-{job.job_key}",
        source_type="direct_url_json_ld",
        source_url=url,
        canonical_url=url,
        source_title=job.title if correct else "Software Engineer",
        source_company=job.company if correct else "Different Company",
        source_location=job.location,
        description_text="Responsibilities include strategic planning and executive partnership. Qualifications include eight years of experience.",
        raw_content_hash=f"hash-{url}",
    )


def test_candidate_page_budget_preserves_search_relevance_order(monkeypatch):
    job, queue = make_job_and_queue()
    relevant_first = "https://careers.example.com/jobs/zzz-relevant"
    alphabetically_first = "https://careers.example.com/jobs/aaa-unrelated"
    client = FakeSheetClient([job], [queue])
    fetcher = MappingFetcher()

    def extracted(fetched, **_kwargs):
        return evidence_for(job, fetched.final_url, correct=fetched.final_url == relevant_first)

    monkeypatch.setattr("src.enrichment.search_run.extract_job_evidence", extracted)

    summary = run_external_search_enrichment(
        client,
        configs=[company_config()],
        provider=OrderedProvider([relevant_first, alphabetically_first]),
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
        query_budget=1,
        candidate_page_budget=1,
    )

    assert fetcher.urls == [relevant_first]
    assert summary.enriched == 1


def test_manual_candidate_rejects_invalid_url_before_any_work():
    job, queue = make_job_and_queue()
    client = FakeSheetClient([job], [queue])

    with pytest.raises(ValueError, match="safe public HTTP or HTTPS"):
        run_external_search_enrichment(
            client,
            configs=[company_config()],
            priority_rules={},
            now=NOW,
            job_key=job.job_key,
            candidate_urls=["file:///tmp/posting.html"],
        )


def test_manual_candidate_reports_missing_job_instead_of_silent_zero_summary():
    job, queue = make_job_and_queue()
    client = FakeSheetClient([job], [queue])

    with pytest.raises(ValueError, match="job was not found"):
        run_external_search_enrichment(
            client,
            configs=[company_config()],
            priority_rules={},
            now=NOW,
            job_key="missing-job",
            candidate_urls=["https://careers.example.com/jobs/123"],
        )


def test_real_json_ld_page_passes_extraction_and_match_validation():
    job, queue = make_job_and_queue()
    client = FakeSheetClient([job], [queue])
    url = "https://careers.example.com/jobs/123"
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Manager, Strategic Planning",
        "hiringOrganization": {"@type": "Organization", "name": "Example Company"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Dallas",
                "addressRegion": "TX",
            },
        },
        "description": "Responsibilities include strategic planning, growth ownership, executive partnership, and team leadership. Qualifications include eight years of experience.",
        "url": url,
    }
    html = f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'
    fetcher = MappingFetcher({url: html})

    summary = run_external_search_enrichment(
        client,
        configs=[company_config()],
        provider=OrderedProvider([url]),
        fetcher=fetcher,
        priority_rules={},
        now=NOW,
        query_budget=1,
    )

    assert summary.enriched == 1
    assert client.tables["Jobs"][0]["canonical_url"] == url
    accepted = [row for row in client.tables["Enrichment_Evidence"] if row["source_type"] == "external_search_page"]
    assert len(accepted) == 1
    assert accepted[0]["accepted"] is True
