import pytest

from src.models import JOB_FIELDS, SPRINT_36_REVIEW_JOB_FIELDS, SPRINT_37_DECISION_JOB_FIELDS, JobPosting
from src.review_workflow import (
    apply_review_update,
    build_feedback_metrics,
    build_review_dashboard_sections,
    merge_review_state,
    sorted_for_action_queue,
    validate_review_transition,
)


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX",
        "status": "open",
        "total_score": 80,
        "alert_tier": "strong_fit",
        "score_status": "verified",
        "verified_total_score": 80,
        "potential_priority_score": 70,
        "potential_priority": "high",
        "role_family": "Commercial Strategy",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-27",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_review_schema_fields_stay_before_sprint37_decision_fields():
    review_start = -len(SPRINT_36_REVIEW_JOB_FIELDS) - len(SPRINT_37_DECISION_JOB_FIELDS)
    review_end = -len(SPRINT_37_DECISION_JOB_FIELDS)

    assert JOB_FIELDS[review_start:review_end] == SPRINT_36_REVIEW_JOB_FIELDS
    assert JOB_FIELDS[-len(SPRINT_37_DECISION_JOB_FIELDS):] == SPRINT_37_DECISION_JOB_FIELDS


def test_valid_review_transition_allows_review_to_interest():
    validate_review_transition("review_now", "interested")


def test_invalid_review_transition_rejects_applied_regression():
    with pytest.raises(ValueError, match="Invalid review transition"):
        validate_review_transition("applied", "not_reviewed")


def test_manual_priority_orders_action_queue_ahead_of_automated_score():
    high_score = make_job(job_key="high-score", total_score=95, verified_total_score=95, manual_priority=None)
    manual = make_job(job_key="manual", total_score=60, verified_total_score=60, manual_priority=5)

    assert sorted_for_action_queue([high_score, manual])[0].job_key == "manual"


def test_manual_priority_can_be_removed_without_rewriting_automated_score():
    job = make_job(manual_priority=4, total_score=82, verified_total_score=82)
    updated = apply_review_update(job, manual_priority="")

    assert updated.manual_priority is None
    assert updated.total_score == 82
    assert updated.verified_total_score == 82


def test_dismissal_reason_validation_uses_controlled_values():
    job = make_job(review_status="review_now")
    dismissed = apply_review_update(job, review_status="dismissed", dismissal_reason="commute_too_long")

    assert dismissed.review_status == "dismissed"
    assert dismissed.dismissal_reason == "commute_too_long"
    with pytest.raises(ValueError, match="Invalid dismissal reason"):
        apply_review_update(job, dismissal_reason="bad_reason")


def test_application_state_progression_sets_application_date():
    job = make_job(review_status="interested", reviewed_date="2026-06-26")
    applied = apply_review_update(job, review_status="applied", application_url="https://example.com/apply")

    assert applied.review_status == "applied"
    assert applied.application_status == "applied"
    assert applied.application_date
    assert applied.application_url == "https://example.com/apply"


def test_duplicate_merge_preserves_advanced_manual_state():
    existing = make_job(
        review_status="applied",
        reviewed_date="2026-06-25",
        application_status="applied",
        application_date="2026-06-26",
        application_url="https://example.com/apply",
        resume_version="topgolf-v1",
        review_notes="Submitted application.",
    )
    incoming = make_job(review_status="not_reviewed", reviewed_date="", application_date="", application_url="")

    merged = merge_review_state(existing, incoming)

    assert merged.review_status == "applied"
    assert merged.application_status == "applied"
    assert merged.application_url == "https://example.com/apply"
    assert merged.resume_version == "topgolf-v1"
    assert "Submitted application" in merged.review_notes


def test_duplicate_merge_flags_conflicting_manual_decisions():
    existing = make_job(review_status="dismissed", dismissal_reason="compensation_too_low")
    incoming = make_job(review_status="interested", manual_fit_rating=9)

    merged = merge_review_state(existing, incoming)

    assert merged.manual_decision_conflict.startswith("conflicting_manual_decisions")
    assert merged.dismissal_reason == "compensation_too_low"


def test_review_dashboard_sections_surface_application_queues():
    jobs = [
        make_job(job_key="review", review_status="review_now", manual_priority=4),
        make_job(job_key="interested", review_status="interested"),
        make_job(job_key="deferred", review_status="deferred", follow_up_date="2026-06-27"),
        make_job(job_key="applied", review_status="applied", application_status="applied", next_action="Follow up", next_action_date="2026-06-27"),
        make_job(job_key="interview", review_status="interviewing", application_status="interviewing"),
        make_job(job_key="offer", review_status="offer", application_status="offer"),
    ]

    values = build_review_dashboard_sections(jobs, as_of="2026-06-27")
    flattened = "\n".join(str(cell) for row in values for cell in row)

    for expected in [
        "Review now queue",
        "Interested queue",
        "Deferred follow-ups queue",
        "Applications submitted queue",
        "Interviews in progress queue",
        "Offers queue",
        "Stale applications needing follow-up queue",
        "Upcoming next actions queue",
        "Feedback calibration",
    ]:
        assert expected in flattened


def test_feedback_metrics_compare_automated_score_to_manual_fit():
    jobs = [
        make_job(job_key="dismissed-high", review_status="dismissed", dismissal_reason="weak_p_and_l_path", total_score=88, verified_total_score=88, manual_fit_rating=3),
        make_job(job_key="interested-low", review_status="interested", total_score=45, verified_total_score=45, manual_fit_rating=9),
        make_job(job_key="applied", review_status="applied", application_status="applied", total_score=78, verified_total_score=78, manual_fit_rating=8),
    ]

    metrics = build_feedback_metrics(jobs)

    assert metrics.reviewed_jobs == 3
    assert metrics.interested_jobs == 2
    assert metrics.applied_jobs == 1
    assert metrics.dismissal_reasons == {"weak_p_and_l_path": 1}
    assert metrics.false_positives == 1
    assert metrics.potential_missed_opportunities >= 1
