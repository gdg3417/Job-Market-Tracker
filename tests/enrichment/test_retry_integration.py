from __future__ import annotations

from src.enrichment.fetcher import EnrichmentFetchError
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.enrichment.run import run_direct_link_enrichment
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"
URL = "https://careers.topgolf.com/jobs/123"


class FailingFetcher:
    def fetch(self, _url: str):
        raise EnrichmentFetchError(
            "network_retryable",
            "temporary timeout",
            retryable=True,
            final_url=URL,
        )


class FakeSheetClient:
    def __init__(self, job: JobPosting, queue: list[EnrichmentQueueItem] | None = None):
        self.tables = {
            "Jobs": [job.to_dict()],
            "Enrichment_Queue": [item.to_dict() for item in queue or []],
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


def retry_job() -> JobPosting:
    return JobPosting(
        job_key="topgolf-123",
        company="Topgolf",
        title="Sr Manager, Strategic Planning",
        location="Dallas, TX",
        canonical_url=URL,
        source_job_id="123",
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert",
        status="open",
        potential_priority_score=90,
        potential_priority="high",
        score_status="provisional",
        enrichment_status="pending",
        enrichment_priority="high",
    )


def test_direct_runner_schedules_first_transient_retry_one_day_later():
    client = FakeSheetClient(retry_job())

    summary = run_direct_link_enrichment(
        client,
        fetcher=FailingFetcher(),
        now=NOW,
        priority_rules={},
    )

    assert summary.retryable_failures == 1
    assert client.tables["Enrichment_Queue"][0]["status"] == "retryable_failure"
    assert client.tables["Enrichment_Queue"][0]["attempt_count"] == 1
    assert client.tables["Enrichment_Queue"][0]["next_attempt_at"] == "2026-06-26T18:00:00Z"
    assert client.tables["Jobs"][0]["enrichment_status"] == "retryable_failure"


def test_high_priority_direct_runner_stops_after_eighth_attempt():
    posting = retry_job()
    posting.enrichment_status = "retryable_failure"
    queue_item = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(posting.job_key, posting.canonical_url),
        job_key=posting.job_key,
        company=posting.company,
        title=posting.title,
        location=posting.location,
        source_job_id=posting.source_job_id,
        lead_url=posting.canonical_url,
        priority="high",
        status="retryable_failure",
        current_stage="direct_url",
        attempt_count=7,
        next_attempt_at=NOW,
        last_attempted_at="2026-06-18T18:00:00Z",
        created_at="2026-06-01T18:00:00Z",
        updated_at="2026-06-18T18:00:00Z",
    )
    client = FakeSheetClient(posting, [queue_item])

    summary = run_direct_link_enrichment(
        client,
        fetcher=FailingFetcher(),
        now=NOW,
        priority_rules={},
    )

    assert summary.permanent_failures == 1
    assert client.tables["Enrichment_Queue"][0]["attempt_count"] == 8
    assert client.tables["Enrichment_Queue"][0]["status"] == "permanent_failure"
    assert client.tables["Enrichment_Queue"][0]["next_attempt_at"] == ""
    assert client.tables["Jobs"][0]["enrichment_status"] == "permanent_failure"
