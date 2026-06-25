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
