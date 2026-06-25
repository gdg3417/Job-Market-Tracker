from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.enrichment.run import run_direct_link_enrichment
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"
URL = "https://careers.topgolf.com/jobs/123"


class FakeSheetClient:
    def __init__(self, job, queue):
        self.tables = {
            "Jobs": [job.to_dict()],
            "Enrichment_Queue": [queue.to_dict()],
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


class UnexpectedFetcher:
    def __init__(self):
        self.calls = 0

    def fetch(self, _url):
        self.calls += 1
        raise AssertionError("ineligible job should not be fetched")


def make_job(**overrides):
    values = {
        "job_key": "topgolf-123",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": URL,
        "status": "open",
        "potential_priority": "high",
        "potential_priority_score": 90,
        "score_status": "provisional",
        "enrichment_status": "pending",
        "enrichment_priority": "high",
    }
    values.update(overrides)
    return JobPosting(**values)


def queue_for(job):
    return EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, job.canonical_url),
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        lead_url=job.canonical_url,
        priority="high",
        status="pending",
        current_stage="direct_url",
    )


def assert_not_processed(job):
    queue = queue_for(job)
    client = FakeSheetClient(job, queue)
    fetcher = UnexpectedFetcher()
    summary = run_direct_link_enrichment(
        client,
        fetcher=fetcher,
        now=NOW,
        priority_rules={},
    )
    assert summary.direct_attempts == 0
    assert fetcher.calls == 0
    assert client.tables["Enrichment_Queue"][0]["status"] == "pending"
    assert client.tables["Enrichment_Queue"][0]["attempt_count"] == 0


def test_terminal_job_with_stale_pending_queue_row_is_not_processed():
    assert_not_processed(make_job(status="confirmed_closed", closed_date="2026-06-20"))


def test_verified_job_with_stale_pending_queue_row_is_not_processed():
    assert_not_processed(make_job(score_status="verified", verified_total_score=85))


def test_excluded_job_with_stale_pending_queue_row_is_not_processed():
    assert_not_processed(make_job(potential_priority="excluded", score_status="excluded"))
