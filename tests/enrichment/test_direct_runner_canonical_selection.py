from src.enrichment.fetcher import EnrichmentFetchError
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.enrichment.run import run_direct_link_enrichment
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"
CURRENT_URL = "https://careers.topgolf.com/jobs/123"
OLD_URL = "https://www.linkedin.com/jobs/view/123"


class FakeSheetClient:
    def __init__(self, job, queue):
        self.tables = {
            "Jobs": [job.to_dict()],
            "Enrichment_Queue": [item.to_dict() for item in queue],
            "Enrichment_Evidence": [],
        }

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def read_jobs_with_row_numbers(self):
        return [(2, JobPosting.from_dict(self.tables["Jobs"][0]))]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()


class CountingFetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        raise EnrichmentFetchError(
            "network_retryable",
            "temporary timeout",
            retryable=True,
            final_url=url,
        )


def make_job():
    return JobPosting(
        job_key="topgolf-123",
        company="Topgolf",
        title="Sr Manager, Strategic Planning",
        location="Dallas, TX",
        canonical_url=CURRENT_URL,
        source_job_id="123",
        status="open",
        potential_priority="high",
        potential_priority_score=90,
        score_status="provisional",
        enrichment_status="pending",
        enrichment_priority="high",
    )


def queue_row(job, url, **overrides):
    values = {
        "enrichment_id": enrichment_id_for(job.job_key, url),
        "job_key": job.job_key,
        "company": job.company,
        "title": job.title,
        "lead_url": url,
        "priority": "high",
        "status": "pending",
        "current_stage": "direct_url",
        "created_at": "2026-06-01T18:00:00Z",
        "updated_at": "2026-06-01T18:00:00Z",
    }
    values.update(overrides)
    return EnrichmentQueueItem(**values)


def test_direct_runner_attempts_only_current_canonical_row_when_two_rows_are_due():
    job = make_job()
    old_row = queue_row(job, OLD_URL)
    current_row = queue_row(job, CURRENT_URL)
    client = FakeSheetClient(job, [old_row, current_row])
    fetcher = CountingFetcher()

    summary = run_direct_link_enrichment(
        client,
        fetcher=fetcher,
        now=NOW,
        priority_rules={},
    )

    assert summary.direct_attempts == 1
    assert fetcher.urls == [CURRENT_URL]
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    assert rows[current_row.enrichment_id]["attempt_count"] == 1
    assert rows[old_row.enrichment_id]["attempt_count"] == 0


def test_obsolete_due_row_is_not_processed_while_current_canonical_row_waits_for_retry():
    job = make_job()
    old_row = queue_row(job, OLD_URL)
    current_row = queue_row(
        job,
        CURRENT_URL,
        status="retryable_failure",
        attempt_count=1,
        last_attempted_at=NOW,
        next_attempt_at="2026-06-26T18:00:00Z",
    )
    client = FakeSheetClient(job, [old_row, current_row])
    fetcher = CountingFetcher()

    summary = run_direct_link_enrichment(
        client,
        fetcher=fetcher,
        now=NOW,
        priority_rules={},
    )

    assert summary.direct_attempts == 0
    assert fetcher.urls == []
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    assert rows[current_row.enrichment_id]["attempt_count"] == 1
    assert rows[old_row.enrichment_id]["attempt_count"] == 0
