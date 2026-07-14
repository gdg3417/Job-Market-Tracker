from __future__ import annotations

from src.verification_health import calculate_verification_health
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def _calculate(jobs):
    return calculate_verification_health(
        jobs=jobs,
        job_sources=[],
        queue_rows=[],
        evidence_rows=[],
        runs_rows=[successful_daily_run()],
        as_of=AS_OF,
    )


def test_dismissed_roles_are_excluded_from_actionable_debt_and_sla_breaches():
    dismissed = job(
        "dismissed",
        review_status="dismissed",
        reviewed_date="2026-06-20",
        first_seen_date="2026-06-01",
        created_at="2026-06-01T12:00:00Z",
    )
    result = _calculate([dismissed])

    assert result.actionable_summary["actionable_roles"] == 0
    assert result.actionable_summary["dismissed_roles_excluded"] == 1
    assert result.blocker_counts == {}
    assert result.sla_breaches == []


def test_terminal_job_application_and_hard_exclusion_states_are_not_actionable():
    rows = [
        job("closed", status="confirmed_closed"),
        job("rejected", application_status="rejected"),
        job("withdrawn", application_status="withdrawn"),
        job("blocked", score_explanation="company_exclusion=true"),
        job("senior", role_level="senior_director"),
        job("hard", score_status="excluded"),
    ]
    result = _calculate(rows)

    assert result.actionable_summary["actionable_roles"] == 0
    assert result.actionability_exclusions == {
        "blocked_company": 1,
        "hard_excluded": 1,
        "terminal_application": 2,
        "terminal_job": 1,
        "too_senior_hard_exclusion": 1,
    }


def test_active_applications_remain_actionable_when_not_terminal_or_excluded():
    applied = job(
        "applied",
        score_status="verified",
        review_status="applied",
        application_status="interviewing",
        application_date="2026-06-20",
        verified_total_score=85,
    )
    result = _calculate([applied])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["active_applications"] == 1
    assert result.actionable_summary["actionable_unverified"] == 0


def test_deferred_future_is_excluded_and_deferred_due_is_actionable():
    future = job("future", review_status="deferred", follow_up_date="6/30/26")
    due = job("due", review_status="deferred", follow_up_date="6/25/26")
    missing = job("missing", review_status="deferred", follow_up_date="")
    result = _calculate([future, due, missing])

    assert result.actionable_summary["actionable_roles"] == 2
    assert result.actionable_summary["deferred_not_due_excluded"] == 1
    assert "future" not in result.high_potential_blockers
    assert "due" in result.high_potential_blockers
    assert result.blocker_counts["manual_review_required"] == 1


def test_likely_closed_role_remains_actionable_until_authoritative_closure():
    likely_closed = job(
        "likely",
        status="likely_closed",
        score_status="verified",
        verified_total_score=80,
    )
    result = _calculate([likely_closed])

    assert result.actionable_summary["closure_confirmations_required"] == 1
    assert result.blocker_counts == {"manual_review_required": 1}
    assert result.manual_intervention[0]["job_key"] == "likely"


def test_nonblank_invalid_identity_is_audited_but_not_actionable():
    result = _calculate([
        {},
        {"job_key": "missing-title", "company": "Acme"},
        job("valid"),
    ])

    assert result.records_read["jobs"] == 3
    assert result.portfolio_coverage["invalid_identity_rows"] == 1
    assert result.actionability_exclusions["invalid_job_identity"] == 1
    assert result.actionable_summary["actionable_roles"] == 1


def test_primary_blocker_is_unique_and_secondary_gaps_remain_auditable():
    incomplete = job(
        "incomplete",
        location="",
        salary_min="",
        salary_max="",
        work_model="unknown",
        remote_status="unknown",
        description_text="",
    )
    result = _calculate([incomplete])

    assert sum(result.blocker_counts.values()) == 1
    assert result.blocker_counts == {"enrichment_not_attempted": 1}
    assert result.secondary_gap_counts["no_authoritative_url"] == 1
    assert result.secondary_gap_counts["missing_description"] == 1
    assert result.secondary_gap_counts["missing_location"] == 1
    assert result.secondary_gap_counts["missing_compensation"] == 1
    assert result.secondary_gap_counts["missing_work_model"] == 1


def test_portfolio_coverage_is_retained_separately_from_actionable_health():
    historical = job(
        "historical",
        status="closed",
        score_status="verified",
        canonical_url="https://jobs.acme.com/historical",
        verified_total_score=80,
    )
    actionable = job("actionable")
    result = _calculate([historical, actionable])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.portfolio_coverage["portfolio_jobs"] == 2
    assert result.portfolio_coverage["portfolio_terminal"] == 1
    assert result.portfolio_coverage["portfolio_verified"] == 1
    assert 0 <= result.portfolio_coverage["portfolio_coverage_rate"] <= 1


def test_funnel_conversions_are_bounded_and_non_nested_stages_are_populations():
    rows = [
        job("topgolf", company="Topgolf", score_status="verified", verified_total_score=85),
        job("toyota", company="Toyota", review_status="dismissed", potential_priority="medium"),
        job("applied", review_status="applied", application_status="applied"),
    ]
    result = _calculate(rows)

    assert all(
        metric.conversion_rate is None or 0 <= metric.conversion_rate <= 1
        for metric in result.funnel
    )
    funnel = {metric.stage: metric for metric in result.funnel}
    assert funnel["fully_verified"].metric_type == "population"
    assert funnel["fully_verified"].conversion_rate is None
    assert funnel["jobs_accepted"].metric_type == "conversion"


def test_terminal_noise_does_not_change_actionable_health_classification():
    actionable = job("actionable")
    baseline = _calculate([actionable])
    with_noise = _calculate([
        actionable,
        job("dismissed", review_status="dismissed"),
        job("closed", status="closed"),
        job("rejected", application_status="rejected"),
    ])

    assert with_noise.overall_classification == baseline.overall_classification
    assert with_noise.overall_score == baseline.overall_score
