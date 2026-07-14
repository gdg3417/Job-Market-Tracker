from __future__ import annotations

from src.generated_surface_policy import (
    include_in_current_context,
    include_on_follow_up_queue,
    include_on_review_queue,
)
from src.models import JobPosting


def test_not_interested_decision_is_terminal_even_when_review_status_is_stale():
    job = JobPosting(
        job_key="not-interested-role",
        company="Acme Industrial",
        title="Senior Manager, Strategy",
        canonical_url="https://example.com/jobs/1",
        review_status="not_reviewed",
        interest_decision="not_interested",
        potential_priority="high",
        score_status="verified",
        total_score=80,
    )

    assert include_on_review_queue(job) is False
    assert include_on_follow_up_queue(job) is False
    assert include_in_current_context(job) is False
