from __future__ import annotations

from src.enrichment.fetcher import EnrichmentFetchError
from src.enrichment.lifecycle import LifecycleObservation, run_lifecycle_checks
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.enrichment.run import run_direct_link_enrichment
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"
CURRENT_URL = "https://careers.topgolf.com/jobs/123"
OLD_URL = "https://www.linkedin.com/jobs/view/123"


class FakeSheetClient:
    def __init__(self, job: JobPosting, queue: list[EnrichmentQueueItem] | None = None):
        self.tables = {
            "Jobs": [job.to_dict()],
            "Enrichment_Queue": [item.to_dict() for item in queue or []],
            "Enrichment_Evidence": [],
            "Runs": [],
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


class CountingFailingFetcher:
    def __init__(self):
        self.urls: list[str] = []

    def fetch(self, url: str):
        self.urls.append(url)
        raise EnrichmentFetchError(
            "network_retryable",
            "temporary timeout",
            retryable=True,
            final_url=url,
        )


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "topgolf-123",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": CURRENT_URL,
        "source_job_id": "123",
        "source_primary": "gmail_alert",
        "first_seen_date": "2026-04-01",
        "last_seen_date": "2026-06-20",
        "status": "open",
        "potential_priority": "high",
        "potential_priority_score": 90,
        "score_status": "provisional",
        "enrichment_status": "pending",
        "enrichment_priority": "high",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_reopening_creates_only_current_canonical_cycle_and_runner_attempts_once():
    job = make_job(
        status="confirmed_closed",
        closed_date="2026-06-20",
        enrichment_status="closed",
        enrichment_source_url=CURRENT_URL,
        enrichment_match_confidence=95,
    )
    old_row = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, OLD_URL),
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        lead_url=OLD_URL,
        priority="high",
        status="closed",
        current_stage="external_search",
        attempt_count=8,
        error_type="posting_closed",
    )
    client = FakeSheetClient(job, [old_row])

    def checker(_job, *, checked_at):
        return LifecycleObservation(
            checked_at=checked_at,
            source_type="direct_url",
            source_url=CURRENT_URL,
            authoritative=True,
            http_status=200,
            listed=True,
        )

    lifecycle_summary = run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)
    assert lifecycle_summary.reopened == 1
    assert len(client.tables["Enrichment_Queue"]) == 2

    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    current_id = enrichment_id_for(job.job_key, CURRENT_URL)
    old_id = enrichment_id_for(job.job_key, OLD_URL)
    assert rows[current_id]["status"] == "pending"
    assert rows[current_id]["lead_url"] == CURRENT_URL
    assert rows[current_id]["attempt_count"] == 0
    assert rows[old_id]["status"] == "closed"

    fetcher = CountingFailingFetcher()
    direct_summary = run_direct_link_enrichment(
        client,
        fetcher=fetcher,
        now=NOW,
        priority_rules={},
    )
    assert direct_summary.direct_attempts == 1
    assert fetcher.urls == [CURRENT_URL]
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    assert rows[current_id]["status"] == "retryable_failure"
    assert rows[current_id]["attempt_count"] == 1
    assert rows[old_id]["status"] == "closed"


def test_existing_current_canonical_row_is_reused_and_obsolete_row_stays_closed():
    job = make_job(status="confirmed_closed", closed_date="2026-06-20", enrichment_status="closed")
    current_row = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, CURRENT_URL),
        job_key=job.job_key,
        lead_url=CURRENT_URL,
        status="closed",
        attempt_count=4,
    )
    old_row = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, OLD_URL),
        job_key=job.job_key,
        lead_url=OLD_URL,
        status="closed",
        attempt_count=5,
    )
    client = FakeSheetClient(job, [old_row, current_row])

    def checker(_job, *, checked_at):
        return LifecycleObservation(
            checked_at=checked_at,
            source_type="direct_url",
            source_url=CURRENT_URL,
            authoritative=True,
            http_status=200,
            listed=True,
        )

    run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)
    assert len(client.tables["Enrichment_Queue"]) == 2
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    assert rows[current_row.enrichment_id]["status"] == "pending"
    assert rows[current_row.enrichment_id]["attempt_count"] == 0
    assert rows[old_row.enrichment_id]["status"] == "closed"
    assert rows[old_row.enrichment_id]["attempt_count"] == 5


def test_nonconsecutive_duplicate_evidence_cannot_mutate_job_twice():
    job = make_job(first_seen_date="2026-04-01", lifecycle_next_check_at="")
    client = FakeSheetClient(job)

    absence = LifecycleObservation(
        checked_at=NOW,
        source_type="external_search",
        source_url="https://search.example.com/topgolf-strategy",
        authoritative=False,
        supporting_absence=True,
    )
    temporary_failure = LifecycleObservation(
        checked_at=NOW,
        source_type="direct_url_failure",
        source_url=CURRENT_URL,
        authoritative=True,
        http_status=503,
        error_type="http_retryable",
    )

    run_lifecycle_checks(
        client,
        checker=lambda _job, checked_at: absence,
        now=NOW,
        write_run_record=False,
    )
    assert client.tables["Jobs"][0]["missed_count"] == 1
    client.tables["Jobs"][0]["lifecycle_next_check_at"] = NOW

    run_lifecycle_checks(
        client,
        checker=lambda _job, checked_at: temporary_failure,
        now=NOW,
        write_run_record=False,
    )
    assert client.tables["Jobs"][0]["missed_count"] == 1
    assert client.tables["Jobs"][0]["lifecycle_check_count"] == 2
    client.tables["Jobs"][0]["lifecycle_next_check_at"] = NOW
    before_key = client.tables["Jobs"][0]["lifecycle_last_evidence_key"]

    summary = run_lifecycle_checks(
        client,
        checker=lambda _job, checked_at: absence,
        now=NOW,
        write_run_record=False,
    )
    assert summary.duplicate_observations == 1
    assert summary.jobs_updated == 0
    assert summary.jobs_unchanged == 1
    assert summary.evidence_written == 0
    assert len(client.tables["Enrichment_Evidence"]) == 2
    assert client.tables["Jobs"][0]["missed_count"] == 1
    assert client.tables["Jobs"][0]["lifecycle_check_count"] == 2
    assert client.tables["Jobs"][0]["lifecycle_last_evidence_key"] == before_key
    assert client.tables["Jobs"][0]["lifecycle_next_check_at"] == NOW
