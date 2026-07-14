from src.verification_health import calculate_verification_health
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def _calculate(jobs, *, resolution_rows=None):
    return calculate_verification_health(
        jobs=jobs,
        job_sources=[],
        queue_rows=[],
        evidence_rows=[],
        runs_rows=[successful_daily_run()],
        resolution_rows=resolution_rows or [],
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


def test_active_review_application_state_precedes_stale_dismissal_fields():
    result = _calculate([
        job(
            "review-status-interview",
            score_status="verified",
            review_status="interviewing",
            dismissal_reason="not_interested",
            application_status="",
            application_date="2026-06-20",
            verified_total_score=85,
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["active_applications"] == 1
    assert result.actionable_summary["dismissed_roles_excluded"] == 0


def test_active_application_precedes_hard_lead_exclusions():
    result = _calculate([
        job(
            "active-hard-excluded",
            review_status="interviewing",
            application_status="interviewing",
            score_status="excluded",
            potential_priority="excluded",
            score_explanation="company_exclusion=true; hard_exclude=true",
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["active_applications"] == 1
    assert result.actionability_exclusions == {}


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


def test_deferred_actionability_evaluates_both_supported_due_date_fields():
    result = _calculate([
        job(
            "due-next-action",
            review_status="deferred",
            follow_up_date="6/30/26",
            next_action_date="6/25/26",
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["deferred_not_due_excluded"] == 0
    assert "due-next-action" in result.high_potential_blockers


def test_deferred_date_due_on_current_central_day_is_actionable():
    result = _calculate([
        job(
            "due-today",
            review_status="deferred",
            follow_up_date="2026-06-26",
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.actionable_summary["deferred_not_due_excluded"] == 0


def test_invalid_deferred_date_requires_correction_even_with_another_future_date():
    result = _calculate([
        job(
            "invalid-date",
            review_status="deferred",
            follow_up_date="not-a-date",
            next_action_date="2026-06-30",
        )
    ])

    assert result.actionable_summary["actionable_roles"] == 1
    assert result.blocker_counts == {"manual_review_required": 1}


def test_manual_authoritative_url_is_manual_work_until_resolver_validation():
    result = _calculate([
        job(
            "manual-url",
            manual_authoritative_url="https://jobs.acme.com/manual-url",
        )
    ])

    assert result.blocker_counts == {"manual_review_required": 1}
    assert result.blocker_ownership_counts == {"manual_intervention": 1}
    assert result.actionable_summary["manual_interventions_required"] == 1


def test_validated_manual_authoritative_url_is_not_left_as_manual_url_work():
    result = _calculate(
        [
            job(
                "validated-manual-url",
                score_status="verified",
                verified_total_score=85,
                manual_authoritative_url="https://jobs.acme.com/validated-manual-url",
            )
        ],
        resolution_rows=[{
            "job_key": "validated-manual-url",
            "resolution_state": "manual_override",
            "authoritative_url": "https://jobs.acme.com/validated-manual-url",
            "match_confidence": 100,
            "updated_at": "2026-06-26T17:00:00Z",
        }],
    )

    assert result.blocker_counts == {}
    assert result.actionable_summary["manual_interventions_required"] == 0
