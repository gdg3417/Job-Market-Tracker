from __future__ import annotations

from typing import Any

from src.job_upsert import upsert_jobs
from src.models import JobPosting
from src.normalize import normalize_raw_job


class FakeSheetClient:
    def __init__(self, jobs: list[dict[str, Any]] | None = None, sources: list[dict[str, Any]] | None = None):
        self.jobs = jobs or []
        self.sources = sources or []
        self.rejected: list[dict[str, Any]] = []
        self.updated_jobs: list[tuple[int, dict[str, Any]]] = []
        self.updated_sources: list[tuple[int, dict[str, Any]]] = []

    def read_records_with_row_numbers(self, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
        if worksheet_name == "Jobs":
            return [(index + 2, record) for index, record in enumerate(self.jobs)]
        if worksheet_name == "Job_Sources":
            return [(index + 2, record) for index, record in enumerate(self.sources)]
        return []

    def append_job(self, job: JobPosting) -> None:
        self.jobs.append(job.to_dict())

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.jobs[row_number - 2] = job.to_dict()
        self.updated_jobs.append((row_number, job.to_dict()))

    def append_job_source(self, record: dict[str, Any]) -> None:
        self.sources.append(dict(record))

    def update_job_source(self, row_number: int, record: dict[str, Any]) -> None:
        self.sources[row_number - 2] = dict(record)
        self.updated_sources.append((row_number, dict(record)))

    def append_records(self, worksheet_name: str, records: list[dict[str, Any]]) -> None:
        if worksheet_name == "Rejected_Jobs":
            self.rejected.extend(dict(record) for record in records)


def make_job(
    *,
    source_primary: str = "greenhouse",
    source_job_id: str = "job-1",
    url: str = "https://example.com/jobs/director-revenue-strategy-12345",
    title: str = "Director, Revenue Strategy",
    company: str = "Acme",
    location: str = "Dallas, TX",
) -> JobPosting:
    return normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": location,
            "url": url,
            "source_job_id": source_job_id,
            "description": "Own revenue growth, margin expansion, and executive operating cadence.",
        },
        source_primary=source_primary,
        seen_date="2026-06-16",
    )


def test_upsert_inserts_new_job_and_source():
    client = FakeSheetClient()

    summary = upsert_jobs(client, [make_job()], seen_date="2026-06-16")

    assert summary.jobs_created == 1
    assert summary.jobs_updated == 0
    assert summary.job_sources_created == 1
    assert len(client.jobs) == 1
    assert len(client.sources) == 1
    assert client.jobs[0]["last_seen_date"] == "2026-06-16"
    assert client.sources[0]["job_key"] == client.jobs[0]["job_key"]


def test_upsert_same_job_twice_does_not_create_duplicate_job_or_source():
    client = FakeSheetClient()
    job = make_job()
    repeated_job = make_job()

    summary = upsert_jobs(client, [job, repeated_job], seen_date="2026-06-16")

    assert summary.records_seen == 2
    assert summary.jobs_created == 1
    assert summary.duplicates_matched == 1
    assert len(client.jobs) == 1
    assert len(client.sources) == 1


def test_upsert_same_job_from_two_sources_creates_one_job_and_two_sources():
    client = FakeSheetClient()
    greenhouse_job = make_job(source_primary="greenhouse", source_job_id="gh-1")
    lever_job = make_job(source_primary="lever", source_job_id="lever-1", url="https://jobs.lever.co/acme/8f9a7b6c5d4e3f2a1b")

    summary = upsert_jobs(client, [greenhouse_job, lever_job], seen_date="2026-06-16")

    assert summary.jobs_created == 1
    assert summary.duplicates_matched == 1
    assert summary.job_sources_created == 2
    assert len(client.jobs) == 1
    assert len(client.sources) == 2
    assert {source["source_primary"] for source in client.sources} == {"greenhouse", "lever"}
    assert {source["job_key"] for source in client.sources} == {client.jobs[0]["job_key"]}


def test_upsert_existing_job_preserves_first_seen_and_updates_last_seen():
    existing = make_job()
    existing.first_seen_date = "2026-06-01"
    existing.last_seen_date = "2026-06-01"
    client = FakeSheetClient(jobs=[existing.to_dict()])
    incoming = make_job(title="Director Revenue Strategy")

    summary = upsert_jobs(client, [incoming], seen_date="2026-06-16")

    assert summary.jobs_created == 0
    assert summary.jobs_updated == 1
    assert client.jobs[0]["first_seen_date"] == "2026-06-01"
    assert client.jobs[0]["last_seen_date"] == "2026-06-16"
    assert client.jobs[0]["status"] == "open"


def test_upsert_existing_source_updates_last_seen_instead_of_appending():
    existing = make_job()
    source_record = {
        "source_key": "",
        "job_key": existing.job_key,
        "source_primary": "greenhouse",
        "source_job_id": "job-1",
        "canonical_url": "https://example.com/jobs/1",
        "first_seen_date": "2026-06-01",
        "last_seen_date": "2026-06-01",
    }
    client = FakeSheetClient(jobs=[existing.to_dict()], sources=[source_record])

    summary = upsert_jobs(client, [make_job()], seen_date="2026-06-16")

    assert summary.job_sources_created == 0
    assert summary.job_sources_updated == 1
    assert len(client.sources) == 1
    assert client.sources[0]["first_seen_date"] == "2026-06-01"
    assert client.sources[0]["last_seen_date"] == "2026-06-16"


def test_upsert_confirmed_closed_job_seen_again_becomes_reopened():
    existing = make_job()
    existing.first_seen_date = "2026-06-01"
    existing.last_seen_date = "2026-06-10"
    existing.missed_count = 2
    existing.status = "confirmed_closed"
    existing.closed_date = "2026-06-12"
    client = FakeSheetClient(jobs=[existing.to_dict()])

    summary = upsert_jobs(client, [make_job()], seen_date="2026-06-16")

    assert summary.jobs_updated == 1
    assert client.jobs[0]["status"] == "reopened"
    assert client.jobs[0]["missed_count"] == 0
    assert client.jobs[0]["closed_date"] == ""
    assert client.jobs[0]["first_seen_date"] == "2026-06-01"
    assert client.jobs[0]["last_seen_date"] == "2026-06-16"


def test_upsert_rejects_bad_job_before_jobs_or_sources_write():
    client = FakeSheetClient()
    good = make_job()
    bad = make_job(
        title="New jobs match your preferences.",
        source_primary="gmail_alert",
        source_job_id="bad-1",
        url="https://www.linkedin.com/jobs/view/4242424242",
    )

    summary = upsert_jobs(client, [good, bad], seen_date="2026-06-16")

    assert summary.records_seen == 2
    assert summary.jobs_created == 1
    assert len(client.jobs) == 1
    assert len(client.sources) == 1
    assert len(client.rejected) == 1
    assert client.rejected[0]["title"] == "New jobs match your preferences."
    assert "generic_alert_or_search_title" in client.rejected[0]["rejection_reason"]
