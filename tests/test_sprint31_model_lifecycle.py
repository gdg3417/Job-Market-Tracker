import pytest

from src.models import JobPosting


@pytest.mark.parametrize("status", ["confirmed_closed", "closed", "expired"])
def test_terminal_job_is_not_downgraded_by_missed_source_pass(status: str):
    job = JobPosting(
        job_key="terminal-job",
        company="Acme",
        title="Director, Strategy",
        status=status,
        closed_date="2026-06-20",
        missed_count=2,
    )

    job.mark_missed("2026-06-25")

    assert job.status == status
    assert job.closed_date == "2026-06-20"
    assert job.missed_count == 2


@pytest.mark.parametrize("status", ["confirmed_closed", "closed", "expired"])
def test_non_authoritative_seen_event_does_not_reopen_terminal_job(status: str):
    job = JobPosting(
        job_key="terminal-job",
        company="Acme",
        title="Director, Strategy",
        status=status,
        closed_date="2026-06-20",
    )

    job.mark_seen("2026-06-25")

    assert job.status == status
    assert job.closed_date == "2026-06-20"
    assert job.last_seen_date == "2026-06-25"


def test_explicit_authoritative_seen_event_can_reopen_terminal_job():
    job = JobPosting(
        job_key="terminal-job",
        company="Acme",
        title="Director, Strategy",
        status="confirmed_closed",
        closed_date="2026-06-20",
    )

    job.mark_seen("2026-06-25", allow_reopen=True)

    assert job.status == "reopened"
    assert job.closed_date == ""
