from __future__ import annotations

from typing import Any

from src.job_upsert import upsert_jobs
from src.models import JobPosting
from src.normalize import normalize_raw_job


class ReturningRowSheetClient:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.updated_jobs: list[tuple[int, dict[str, Any]]] = []
        self.next_written_row = 27

    def read_records_with_row_numbers(self, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
        if worksheet_name == "Jobs":
            return []
        if worksheet_name == "Job_Sources":
            return []
        return []

    def append_job(self, job: JobPosting) -> int:
        self.jobs.append(job.to_dict())
        return self.next_written_row

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.updated_jobs.append((row_number, job.to_dict()))

    def append_job_source(self, record: dict[str, Any]) -> None:
        self.sources.append(dict(record))

    def update_job_source(self, row_number: int, record: dict[str, Any]) -> None:
        raise AssertionError("Source updates are not expected in this fixture")

    def append_records(self, worksheet_name: str, records: list[dict[str, Any]]) -> None:
        assert worksheet_name == "Rejected_Jobs"
        assert records == []


def make_job() -> JobPosting:
    return normalize_raw_job(
        {
            "company": "Acme",
            "title": "Senior Manager, Strategy",
            "location": "Dallas, TX",
            "url": "https://example.com/jobs/senior-manager-strategy-1",
            "source_job_id": "job-1",
            "description": "Own strategy, pricing, and operating cadence.",
        },
        source_primary="greenhouse",
        seen_date="2026-07-15",
    )


def test_cached_upsert_state_uses_actual_row_returned_by_bounded_append() -> None:
    client = ReturningRowSheetClient()

    first = upsert_jobs(client, [make_job()], seen_date="2026-07-15")
    second = upsert_jobs(client, [make_job()], seen_date="2026-07-16")

    assert first.jobs_created == 1
    assert second.duplicates_matched == 1
    assert second.jobs_updated == 1
    assert client.updated_jobs[0][0] == 27
