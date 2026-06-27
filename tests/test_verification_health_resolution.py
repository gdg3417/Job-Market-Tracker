from src.verification_health import calculate_verification_health
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def test_resolver_attempt_and_authoritative_success_feed_sprint33_funnel():
    jobs = [job("resolved", canonical_url="https://careers.acme.com/job/123")]
    resolutions = [{
        "job_key": "resolved",
        "resolution_state": "resolved_authoritative",
        "authoritative_url": "https://careers.acme.com/job/123",
        "match_confidence": 92,
        "attempted_at": "2026-06-26T15:20:00Z",
        "resolved_at": "2026-06-26T15:20:00Z",
        "candidate_count": 1,
    }]

    result = calculate_verification_health(
        jobs=jobs,
        job_sources=[],
        queue_rows=[],
        evidence_rows=[],
        resolution_rows=resolutions,
        runs_rows=[successful_daily_run()],
        as_of=AS_OF,
    )
    funnel = {metric.stage: metric for metric in result.funnel}

    assert funnel["enrichment_attempted"].current_count == 1
    assert funnel["authoritative_posting_found"].current_count == 1
    assert result.blocker_counts.get("no_authoritative_url", 0) == 0


def test_resolver_state_produces_normalized_reviewable_blocker():
    resolutions = [{
        "job_key": "probable",
        "resolution_state": "resolved_probable",
        "authoritative_url": "https://careers.acme.com/job/123",
        "match_confidence": 76,
        "attempted_at": "2026-06-26T15:20:00Z",
        "error_message": "Candidate is plausible but below the authoritative threshold",
    }]

    result = calculate_verification_health(
        jobs=[job("probable")],
        job_sources=[],
        queue_rows=[],
        evidence_rows=[],
        resolution_rows=resolutions,
        runs_rows=[successful_daily_run()],
        as_of=AS_OF,
    )

    assert result.blocker_counts["authoritative_match_below_threshold"] == 1
    assert result.manual_intervention[0]["job_key"] == "probable"
