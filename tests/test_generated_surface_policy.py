from __future__ import annotations

from src.generated_surface_policy import (
    include_in_current_context,
    include_in_dashboard,
    include_on_follow_up_queue,
    include_on_review_queue,
)
from src.models import JobPosting


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-01",
        "status": "open",
        "review_status": "not_reviewed",
        "application_status": "",
        "role_level": "Senior Manager",
        "potential_priority": "high",
        "potential_priority_score": 80,
        "total_score": 80,
        "alert_tier": "strong_fit",
        "score_status": "verified",
        "verified_total_score": 80,
        "verified_alert_tier": "strong_fit",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_blocked_swooped_role_is_suppressed_from_generated_review_surfaces():
    job = make_job(
        company="Swooped",
        review_status="reviewing",
        score_explanation="company_exclusion=true; company_exclusion_reason=blocked_company",
    )
    assert include_on_review_queue(job) is False
    assert include_on_follow_up_queue(job) is False
    assert include_in_current_context(job) is False
    assert include_in_dashboard(job) is False


def test_consulting_hard_exclusion_is_suppressed():
    job = make_job(
        company="Deloitte",
        score_status="excluded",
        alert_tier="exclude",
        potential_priority="excluded",
        score_explanation="hard_exclude=true; company_exclusion=true",
    )
    assert include_on_review_queue(job) is False
    assert include_in_current_context(job) is False
    assert include_in_dashboard(job) is False


def test_dismissed_role_is_not_reintroduced_by_manual_state():
    job = make_job(
        review_status="dismissed",
        reviewed_date="2026-07-01",
        review_notes="Reviewed and dismissed",
    )
    assert include_on_review_queue(job) is False
    assert include_on_follow_up_queue(job) is False
    assert include_in_current_context(job) is False


def test_closed_and_rejected_roles_are_suppressed():
    closed = make_job(status="confirmed_closed")
    rejected = make_job(application_status="rejected")
    assert include_on_review_queue(closed) is False
    assert include_on_follow_up_queue(closed) is False
    assert include_on_review_queue(rejected) is False
    assert include_in_dashboard(rejected) is False


def test_active_application_remains_in_follow_up_and_context():
    job = make_job(
        review_status="applied",
        application_status="applied",
        application_date="2026-07-01",
    )
    assert include_on_review_queue(job) is True
    assert include_on_follow_up_queue(job) is True
    assert include_in_current_context(job) is True
    assert include_in_dashboard(job) is True


def test_active_application_preserves_follow_up_even_if_company_is_later_blocked():
    job = make_job(
        company="Blocked After Application",
        review_status="applied",
        application_status="interviewing",
        score_explanation="company_exclusion=true; company_exclusion_reason=blocked_company",
    )
    assert include_on_review_queue(job) is False
    assert include_on_follow_up_queue(job) is True
    assert include_in_current_context(job) is True
    assert include_in_dashboard(job) is True
