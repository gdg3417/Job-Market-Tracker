from __future__ import annotations

from src.models import JobPosting
from src.weekly_context import WeeklyDigestConfig
from src.weekly_context_hotfix import build_weekly_context_rows


def make_job(**overrides):
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
        "total_score": 80,
        "alert_tier": "strong_fit",
        "score_status": "verified",
        "verified_total_score": 80,
        "verified_alert_tier": "strong_fit",
        "potential_priority": "high",
        "potential_priority_score": 80,
    }
    values.update(overrides)
    return JobPosting(**values)


def weekly_record():
    return {
        "Week Start": "2026-06-29",
        "Week End": "2026-07-05",
        "Jobs Added": 2,
        "Jobs Reviewed": 1,
        "Jobs Still Not Reviewed Yet": 1,
        "Jobs Applied": 0,
        "Follow-ups Due": 1,
        "Strong Fit Jobs": 2,
        "Stretch Fit Jobs": 0,
        "Auto-Rejected Jobs": 0,
        "Blocked Company Rejects": 0,
    }


def build_rows(jobs, *, limit=5):
    return build_weekly_context_rows(
        [(index + 2, job) for index, job in enumerate(jobs)],
        [weekly_record()],
        as_of="2026-07-10",
        config=WeeklyDigestConfig(top_review_limit=5, top_new_match_limit=limit),
    )


def test_dismissed_match_is_excluded_and_next_open_match_backfills_limit():
    dismissed = make_job(
        job_key="dismissed",
        company="Dismissed Co",
        review_status="dismissed",
        verified_total_score=95,
        total_score=95,
    )
    open_match = make_job(
        job_key="open",
        company="Open Co",
        verified_total_score=75,
        total_score=75,
    )

    rows = build_rows([dismissed, open_match], limit=1)
    matches = [row for row in rows if row["item_type"] == "match"]

    assert len(matches) == 1
    assert matches[0]["company"] == "Open Co"
    assert not any(row.get("company") == "Dismissed Co" for row in rows)


def test_period_distinguishes_completed_week_metrics_from_current_actions():
    rows = build_rows([])
    period = next(row for row in rows if row["item_type"] == "period")

    assert period["value"] == (
        "2026-06-29 through 2026-07-05 (weekly metrics); "
        "current action items as of 2026-07-10"
    )
