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


def test_active_application_takes_precedence_over_stale_dismissed_review_state():
    result = _calculate([
        job(
            "active-with-stale-review",
            score_status="verified",
            review_status="dismissed",
            dismissal_reason="not_interested",
            application_status="interviewing",
            application_date="2026-06-20",
            verified_total_score=85,
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["active_applications"] == 1
    assert result.actionable_summary["dismissed_roles_excluded"] == 0


def test_manual_dismissal_reason_excludes_role_even_when_review_status_is_stale():
    result = _calculate([
        job(
            "dismissal-reason-only",
            review_status="not_reviewed",
            dismissal_reason="company_not_attractive",
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 0
    assert result.actionable_summary["dismissed_roles_excluded"] == 1
    assert result.blocker_counts == {}


def test_malformed_nonblank_row_is_audited_but_excluded_from_all_job_populations():
    malformed = {
        "status": "open",
        "score_status": "verified",
        "review_status": "applied",
        "updated_at": "2026-06-26T12:00:00Z",
    }
    result = _calculate([malformed, job("valid")])
    funnel = {metric.stage: metric for metric in result.funnel}

    assert result.portfolio_coverage["invalid_identity_rows"] == 1
    assert result.actionability_exclusions["invalid_job_identity"] == 1
    assert funnel["jobs_normalized"].current_count == 1
    assert funnel["fully_verified"].current_count == 0
    assert funnel["human_reviewed"].current_count == 0
    assert funnel["applied"].current_count == 0
