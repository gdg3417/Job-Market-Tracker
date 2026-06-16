from __future__ import annotations

from typing import Any

from src.lifecycle import ClosureCheckResult, check_job_url_closed, update_lifecycle_for_missing_jobs
from src.models import JobPosting
from src.normalize import normalize_raw_job


class FakeSheetClient:
    def __init__(self, jobs: list[dict[str, Any]] | None = None):
        self.jobs = jobs or []
        self.updated_jobs: list[tuple[int, dict[str, Any]]] = []

    def read_jobs_with_row_numbers(self) -> list[tuple[int, JobPosting]]:
        return [(index + 2, JobPosting.from_dict(record)) for index, record in enumerate(self.jobs)]

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.jobs[row_number - 2] = job.to_dict()
        self.updated_jobs.append((row_number, job.to_dict()))


def make_job(
    *,
    status: str = "open",
    first_seen_date: str = "2026-06-01",
    last_seen_date: str = "2026-06-15",
    missed_count: int = 0,
    url: str = "https://example.com/jobs/1",
) -> JobPosting:
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Director, Revenue Strategy",
            "location": "Dallas, TX",
            "url": url,
            "source_job_id": "job-1",
            "description": "Own revenue growth and executive operating cadence.",
        },
        source_primary="greenhouse",
        seen_date=first_seen_date,
    )
    job.first_seen_date = first_seen_date
    job.last_seen_date = last_seen_date
    job.status = status
    job.missed_count = missed_count
    job.closed_date = ""
    return job


def test_missing_job_once_becomes_not_seen_once():
    client = FakeSheetClient(jobs=[make_job().to_dict()])

    summary = update_lifecycle_for_missing_jobs(client, run_date="2026-06-16")

    assert summary.records_checked == 1
    assert summary.jobs_not_seen == 1
    assert summary.jobs_marked_not_seen_once == 1
    assert summary.rows_updated == 1
    assert client.jobs[0]["status"] == "not_seen_once"
    assert client.jobs[0]["missed_count"] == 1
    assert client.jobs[0]["closed_date"] == ""
    assert client.jobs[0]["days_open"] == 15


def test_missing_job_twice_becomes_likely_closed_without_confirming_if_url_looks_active():
    job = make_job(status="not_seen_once", missed_count=1)
    client = FakeSheetClient(jobs=[job.to_dict()])

    summary = update_lifecycle_for_missing_jobs(
        client,
        run_date="2026-06-16",
        url_checker=lambda _: ClosureCheckResult(checked=True, is_closed=False, reason="still_active"),
    )

    assert summary.jobs_marked_likely_closed == 1
    assert summary.jobs_confirmed_closed == 0
    assert summary.url_checks_attempted == 1
    assert client.jobs[0]["status"] == "likely_closed"
    assert client.jobs[0]["missed_count"] == 2
    assert client.jobs[0]["closed_date"] == ""


def test_likely_closed_job_with_clear_url_closure_signal_becomes_confirmed_closed():
    job = make_job(status="not_seen_once", missed_count=1)
    client = FakeSheetClient(jobs=[job.to_dict()])

    summary = update_lifecycle_for_missing_jobs(
        client,
        run_date="2026-06-16",
        url_checker=lambda _: True,
    )

    assert summary.jobs_marked_likely_closed == 1
    assert summary.jobs_confirmed_closed == 1
    assert client.jobs[0]["status"] == "confirmed_closed"
    assert client.jobs[0]["closed_date"] == "2026-06-16"
    assert client.jobs[0]["days_open"] == 15


def test_job_seen_on_current_run_is_not_marked_missed():
    client = FakeSheetClient(jobs=[make_job(last_seen_date="2026-06-16").to_dict()])

    summary = update_lifecycle_for_missing_jobs(client, run_date="2026-06-16")

    assert summary.jobs_seen_current_run == 1
    assert summary.rows_updated == 0
    assert client.jobs[0]["status"] == "open"
    assert client.jobs[0]["missed_count"] == 0


def test_confirmed_closed_jobs_are_not_reopened_by_lifecycle_when_not_seen():
    job = make_job(status="confirmed_closed", missed_count=2)
    job.closed_date = "2026-06-15"
    client = FakeSheetClient(jobs=[job.to_dict()])

    summary = update_lifecycle_for_missing_jobs(client, run_date="2026-06-16")

    assert summary.jobs_already_closed == 1
    assert summary.rows_updated == 0
    assert client.jobs[0]["status"] == "confirmed_closed"
    assert client.jobs[0]["closed_date"] == "2026-06-15"


def test_check_job_url_closed_detects_closure_phrase():
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "This job is no longer available."

    class FakeSession:
        def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            return FakeResponse()

    result = check_job_url_closed(make_job(), session=FakeSession())

    assert result.checked is True
    assert result.is_closed is True
    assert result.reason.startswith("closure_phrase")
