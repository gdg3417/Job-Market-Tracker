from __future__ import annotations

from src.models import JobPosting
from src.presentation_refresh import review_queue_snapshot_rows


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-01",
        "status": "open",
        "review_status": "not_reviewed",
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


def test_review_exclusions_do_not_shorten_canonical_jobs_filter_extent():
    open_job = make_job(job_key="open-role")
    blocked_job = make_job(
        job_key="blocked-role",
        company="Swooped",
        review_status="reviewing",
        score_explanation="company_exclusion=true; company_exclusion_reason=blocked_company",
    )
    rows = review_queue_snapshot_rows([(2, open_job), (3, blocked_job)])

    assert len(rows) == 2
    assert rows[0] == (2, open_job)
    assert rows[1][0] == 3
    assert rows[1][1].job_key == ""
    assert rows[1][1].company == ""
    assert rows[1][1].title == ""
    assert rows[1][1].canonical_url == ""
