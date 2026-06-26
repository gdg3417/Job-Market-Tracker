from src.verification_health import calculate_verification_health
from tests.verification_health_helpers import AS_OF, job


def test_daily_completion_uses_full_central_calendar_date_for_funnel_counts():
    daily_completion = {
        "run_id": "daily_workflow_completion_20260626T173000Z",
        "run_type": "daily_workflow_completion",
        "source_name": "GitHub Actions daily run",
        "status": "success",
        "started_at": "2026-06-26T17:30:00Z",
        "finished_at": "2026-06-26T17:30:00Z",
        "notes": '{"central_date":"2026-06-26"}',
    }
    jobs = [
        job(
            "today",
            score_status="verified",
            verified_total_score=80,
            enrichment_completed_at="2026-06-26T15:30:00Z",
            canonical_url="https://jobs.acme.com/today",
            enrichment_source_url="https://jobs.acme.com/today",
            enrichment_match_confidence=95,
        ),
        job(
            "yesterday",
            score_status="verified",
            verified_total_score=80,
            enrichment_completed_at="2026-06-25T15:30:00Z",
            canonical_url="https://jobs.acme.com/yesterday",
            enrichment_source_url="https://jobs.acme.com/yesterday",
            enrichment_match_confidence=95,
        ),
    ]
    result = calculate_verification_health(
        jobs=jobs,
        job_sources=[],
        queue_rows=[],
        evidence_rows=[],
        runs_rows=[daily_completion],
        as_of=AS_OF,
    )
    funnel = {metric.stage: metric for metric in result.funnel}
    assert funnel["fully_verified"].latest_daily_count == 1
    assert funnel["fully_verified"].latest_seven_day_count == 2
