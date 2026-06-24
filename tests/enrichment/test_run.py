from __future__ import annotations

import json

from src.enrichment.fetcher import EnrichmentFetchError, FetchResult
from src.enrichment.run import run_direct_link_enrichment
from src.models import JobPosting

NOW = "2026-06-23T18:00:00Z"


def job_html(title: str, company: str, location: str) -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": location.split(",")[0], "addressRegion": "TX", "addressCountry": "US"},
        },
        "description": (
            "Responsibilities include leading strategic planning, owning growth priorities, and managing a team. "
            "The role partners with executives and drives operating decisions. Qualifications include eight years "
            "of experience, a bachelor's degree, and demonstrated team leadership across multiple functions."
        ),
        "employmentType": "FULL_TIME",
        "datePosted": "2026-06-20",
        "url": "https://careers.example.com/jobs/123",
    }
    return f'<html><body><script type="application/ld+json">{json.dumps(posting)}</script></body></html>'


class FakeFetcher:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def fetch(self, url: str):
        self.calls.append(url)
        outcome = self.outcomes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSheetClient:
    def __init__(self, jobs):
        self.tables = {
            "Jobs": [job.to_dict() for job in jobs],
            "Enrichment_Queue": [],
            "Enrichment_Evidence": [],
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


def queued_job(job_key: str, company: str, title: str, url: str, location: str) -> JobPosting:
    return JobPosting(
        job_key=job_key,
        company=company,
        title=title,
        location=location,
        canonical_url=url,
        source_job_id=job_key,
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert",
        status="open",
        potential_priority_score=85,
        potential_priority="high",
        score_status="provisional",
        enrichment_status="pending",
        enrichment_priority="high",
    )


def fetch_result(url: str, title: str, company: str, location: str) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text=job_html(title, company, location),
    )


def test_topgolf_and_toyota_are_processed_without_creating_duplicate_jobs():
    topgolf_url = "https://careers.example.com/jobs/topgolf-1"
    toyota_url = "https://careers.example.com/jobs/toyota-1"
    client = FakeSheetClient(
        [
            queued_job("topgolf-1", "Topgolf", "Sr Manager, Strategic Planning", topgolf_url, "Dallas, TX"),
            queued_job("toyota-1", "Toyota North America", "National Manager, Product", toyota_url, "Plano, TX"),
        ]
    )
    fetcher = FakeFetcher(
        {
            topgolf_url: fetch_result(topgolf_url, "Senior Manager, Strategic Planning", "Topgolf", "Dallas, TX"),
            toyota_url: fetch_result(toyota_url, "National Manager, Product", "Toyota North America", "Plano, TX"),
        }
    )
    summary = run_direct_link_enrichment(client, fetcher=fetcher, now=NOW, priority_rules={})
    assert summary.jobs_enqueued == 2
    assert summary.enriched == 2
    assert len(client.tables["Jobs"]) == 2
    assert len(client.tables["Enrichment_Queue"]) == 2
    assert len(client.tables["Enrichment_Evidence"]) == 2
    assert {row["enrichment_status"] for row in client.tables["Jobs"]} == {"enriched"}


def test_repeated_run_is_idempotent():
    url = "https://careers.example.com/jobs/toyota-1"
    client = FakeSheetClient([queued_job("toyota-1", "Toyota North America", "National Manager, Product", url, "Plano, TX")])
    fetcher = FakeFetcher({url: fetch_result(url, "National Manager, Product", "Toyota North America", "Plano, TX")})
    first = run_direct_link_enrichment(client, fetcher=fetcher, now=NOW, priority_rules={})
    second = run_direct_link_enrichment(client, fetcher=fetcher, now="2026-06-23T19:00:00Z", priority_rules={})
    assert first.direct_attempts == 1
    assert second.direct_attempts == 0
    assert len(client.tables["Jobs"]) == 1
    assert len(client.tables["Enrichment_Queue"]) == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1


def test_mismatched_posting_is_audited_but_not_merged():
    url = "https://careers.example.com/jobs/toyota-1"
    client = FakeSheetClient([queued_job("toyota-1", "Toyota North America", "National Manager, Product", url, "Plano, TX")])
    fetcher = FakeFetcher({url: fetch_result(url, "Staff Accountant", "Unrelated Software Company", "Austin, TX")})
    summary = run_direct_link_enrichment(client, fetcher=fetcher, now=NOW, priority_rules={})
    job = client.tables["Jobs"][0]
    evidence = client.tables["Enrichment_Evidence"][0]
    assert summary.not_found == 1
    assert job["description_text"] == "Extracted from Gmail job alert"
    assert job["enrichment_status"] == "not_found"
    assert evidence["accepted"] is False
    assert evidence["rejection_reason"]


def test_failure_of_one_enrichment_does_not_stop_other_jobs():
    bad_url = "https://careers.example.com/jobs/bad"
    good_url = "https://careers.example.com/jobs/good"
    client = FakeSheetClient(
        [
            queued_job("bad", "Topgolf", "Sr Manager, Strategic Planning", bad_url, "Dallas, TX"),
            queued_job("good", "Toyota North America", "National Manager, Product", good_url, "Plano, TX"),
        ]
    )
    fetcher = FakeFetcher(
        {
            bad_url: EnrichmentFetchError("network_retryable", "temporary timeout", retryable=True),
            good_url: fetch_result(good_url, "National Manager, Product", "Toyota North America", "Plano, TX"),
        }
    )
    summary = run_direct_link_enrichment(client, fetcher=fetcher, now=NOW, priority_rules={})
    assert summary.direct_attempts == 2
    assert summary.retryable_failures == 1
    assert summary.enriched == 1
    statuses = {row["job_key"]: row["enrichment_status"] for row in client.tables["Jobs"]}
    assert statuses == {"bad": "retryable_failure", "good": "enriched"}
