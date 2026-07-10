from src.follow_up import evaluate_follow_up
from src.models import JobPosting


def test_either_explicit_follow_up_date_triggers_when_the_other_is_later():
    job = JobPosting(
        job_key="explicit-dates",
        company="Acme Industrial",
        title="Senior Manager, Strategy",
        application_status="applied",
        application_date="2026-07-08",
        follow_up_date="2026-07-01",
        next_action_date="2026-07-20",
    )

    result = evaluate_follow_up(job, as_of="2026-07-10")

    assert result.follow_up_due is True
    assert "2026-07-01" in result.follow_up_reason
