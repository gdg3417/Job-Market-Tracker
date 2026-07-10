from __future__ import annotations

from src.models import JobPosting
from src.weekly_context import WeeklyDigestConfig, build_weekly_context_rows


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
        "Jobs Added": 1,
        "Jobs Reviewed": 0,
        "Jobs Still Not Reviewed Yet": 1,
        "Jobs Applied": 0,
        "Follow-ups Due": 0,
        "Strong Fit Jobs": 1,
        "Stretch Fit Jobs": 0,
        "Auto-Rejected Jobs": 0,
        "Blocked Company Rejects": 0,
    }


def build_rows(jobs):
    return build_weekly_context_rows(
        [(index + 2, job) for index, job in enumerate(jobs)],
        [weekly_record()],
        as_of="2026-07-06",
        config=WeeklyDigestConfig(top_review_limit=5, top_new_match_limit=5),
    )


def test_terminal_new_match_is_not_recommended_in_email_contract():
    rows = build_rows([make_job(status="confirmed_closed")])

    assert not any(row["item_type"] == "match" for row in rows)
    assert not any(row["item_type"] == "review" for row in rows)


def test_open_new_match_remains_visible_after_terminal_guard():
    rows = build_rows([make_job(status="open")])
    matches = [row for row in rows if row["item_type"] == "match"]

    assert len(matches) == 1
    assert matches[0]["company"] == "Acme Industrial"
    assert matches[0]["fit_type"] == "Strong Fit"


def test_excluded_new_match_is_not_recommended_even_with_high_score():
    rows = build_rows(
        [
            make_job(
                score_status="excluded",
                alert_tier="exclude",
                verified_alert_tier="exclude",
                score_explanation="hard_exclude=true",
            )
        ]
    )

    assert not any(row["item_type"] in {"match", "review"} for row in rows)
