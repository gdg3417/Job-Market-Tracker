from src.verification_health import BLOCKER_REASONS, HealthThresholds, calculate_verification_health, classify_blocker
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def test_mixed_funnel_aging_and_blockers_are_calculated():
    jobs = [
        job("provisional"),
        job(
            "partial", score_status="partially_verified", enrichment_status="partial",
            enrichment_last_attempted_at="2026-06-25T12:00:00Z",
            canonical_url="https://jobs.acme.com/partial",
            enrichment_source_url="https://jobs.acme.com/partial",
            enrichment_match_confidence=90,
            description_text="Detailed role description " * 20,
            salary_min=170000, work_model="hybrid", remote_status="hybrid",
        ),
        job(
            "verified", score_status="verified", enrichment_status="enriched",
            verified_total_score=82, enrichment_completed_at="2026-06-26T15:30:00Z",
            canonical_url="https://jobs.acme.com/verified",
            enrichment_source_url="https://jobs.acme.com/verified",
            enrichment_match_confidence=95, evidence_completeness_score=90,
        ),
        job("dismissed", potential_priority="medium", review_status="dismissed", reviewed_date="2026-06-26T15:45:00Z"),
        job(
            "applied", score_status="verified", review_status="applied",
            application_status="applied", application_date="2026-06-26T15:50:00Z",
            verified_total_score=88,
        ),
        job("closed", status="confirmed_closed", closed_date="2026-06-26T15:55:00Z"),
    ]
    queue = [{
        "job_key": "partial", "status": "partial", "attempt_count": 1,
        "matched_url": "https://jobs.acme.com/partial", "match_confidence": 90,
        "last_attempted_at": "2026-06-25T12:00:00Z", "updated_at": "2026-06-25T12:00:00Z",
    }]
    evidence = [{
        "job_key": "partial", "accepted": "TRUE",
        "canonical_url": "https://jobs.acme.com/partial", "retrieved_at": "2026-06-25T12:00:00Z",
    }]
    result = calculate_verification_health(
        jobs=jobs,
        job_sources=[{"job_key": "provisional", "created_at": "2026-06-26T15:10:00Z"}],
        queue_rows=queue,
        evidence_rows=evidence,
        runs_rows=[successful_daily_run()],
        target_company_rows=[{"company_name": "Acme Industrial", "priority_tier": "Tier 1", "active": "TRUE"}],
        as_of=AS_OF,
    )
    funnel = {metric.stage: metric for metric in result.funnel}
    expected = {
        "leads_received": 1, "jobs_normalized": 6, "high_potential": 4,
        "enrichment_attempted": 1, "evidence_accepted": 1,
        "partially_verified": 1, "fully_verified": 2,
        "verified_strong_fit": 2, "human_reviewed": 2,
        "applied": 1, "dismissed": 1, "closed": 1,
    }
    for stage, count in expected.items():
        assert funnel[stage].current_count == count
    assert funnel["fully_verified"].denominator_stage == "evidence_accepted"
    assert result.blocker_counts["enrichment_not_attempted"] >= 1
    assert result.oldest_high_potential
    assert result.oldest_target_company


def test_blocker_classifier_covers_normalized_reasons():
    base = job("blocked")
    cases = [
        ({"status": "retryable_failure", "next_attempt_at": "2026-06-27T12:00:00Z"}, "retry_scheduled"),
        ({"status": "permanent_failure", "error_type": "timeout"}, "source_timeout"),
        ({"status": "permanent_failure", "error_type": "source blocked"}, "source_blocked"),
        ({"status": "permanent_failure", "error_type": "parser failure"}, "parser_failure"),
        ({"status": "ambiguous"}, "authoritative_match_below_threshold"),
        ({"status": "not_found", "attempt_count": 1}, "source_not_found"),
        ({"status": "pending", "attempt_count": 0}, "enrichment_not_attempted"),
    ]
    for queue, expected in cases:
        blocker = classify_blocker(base, queue, [], as_of=AS_OF, thresholds=HealthThresholds())
        assert blocker.reason == expected
        assert blocker.reason in BLOCKER_REASONS


def test_latest_daily_counts_use_stage_timestamps():
    jobs = [
        job(
            "new", score_status="verified", verified_total_score=80,
            enrichment_completed_at="2026-06-26T15:30:00Z",
            canonical_url="https://jobs.acme.com/new",
            enrichment_source_url="https://jobs.acme.com/new",
            enrichment_match_confidence=95,
        ),
        job(
            "old", score_status="verified", verified_total_score=80,
            enrichment_completed_at="2026-06-25T15:30:00Z",
            canonical_url="https://jobs.acme.com/old",
            enrichment_source_url="https://jobs.acme.com/old",
            enrichment_match_confidence=95,
        ),
    ]
    result = calculate_verification_health(
        jobs=jobs, job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    funnel = {metric.stage: metric for metric in result.funnel}
    assert funnel["fully_verified"].latest_daily_count == 1
    assert funnel["fully_verified"].latest_seven_day_count == 2


def test_aging_categories_overlap_and_medium_signal_uses_three_day_service_level():
    result = calculate_verification_health(
        jobs=[job(
            "medium", potential_priority="medium", title="Senior Manager, Strategy",
            first_seen_date="2026-06-20", created_at="2026-06-20T12:00:00Z",
        )],
        job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    aging = {metric.category: metric for metric in result.aging}
    assert aging["medium_potential_high_signal"].service_level_hours == 72
    assert aging["medium_potential_high_signal"].breach_count == 1
    assert aging["no_authoritative_url"].current_count == 1
    assert aging["no_successful_enrichment_attempt"].current_count == 1


def test_config_company_without_priority_tier_is_not_target_company():
    result = calculate_verification_health(
        jobs=[job("ordinary")], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], target_company_rows=[],
        config_company_rows=[{"company_name": "Acme Industrial", "active": "TRUE"}],
        as_of=AS_OF,
    )
    assert result.oldest_target_company == []


def test_default_run_id_is_deterministic_for_latest_daily_run():
    kwargs = dict(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run(run_id="daily:2026/06/26")], as_of=AS_OF,
    )
    assert calculate_verification_health(**kwargs).run_id == "sprint33_verification_health_daily_2026_06_26"
    assert calculate_verification_health(**kwargs).run_id == calculate_verification_health(**kwargs).run_id
