from __future__ import annotations

from typing import Any

from src.lifecycle_scheduler import build_priority_lifecycle_plan, target_company_keys
from src.models import JobPosting


def make_job(**overrides: Any) -> JobPosting:
    values = {
        "job_key": "job",
        "company": "Acme",
        "title": "Director, Strategy",
        "location": "Dallas, TX",
        "status": "open",
        "potential_priority": "medium",
        "score_status": "partially_verified",
        "review_status": "not_reviewed",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-26",
        "lifecycle_last_checked_at": "",
        "lifecycle_next_check_at": "",
    }
    values.update(overrides)
    return JobPosting.from_dict(values)


def test_target_company_keys_use_active_priority_and_boost_rows():
    keys = target_company_keys(
        [
            {"company_name": "Acme", "priority_tier": "Tier 1", "active": "TRUE"},
            {"company_name": "Beta", "priority_tier": "", "score_boost_points": "15", "active": "TRUE"},
            {"company_name": "Inactive", "priority_tier": "Tier 1", "active": "FALSE"},
            {"company_name": "Low", "priority_tier": "Low", "score_boost_points": "0", "active": "TRUE"},
        ]
    )

    assert keys == {"acme", "beta"}


def test_priority_lifecycle_plan_orders_by_sprint_38_cadence():
    jobs = [
        make_job(job_key="low", company="Other", potential_priority="low", score_status="provisional"),
        make_job(job_key="target", company="Acme", potential_priority="medium"),
        make_job(job_key="high", company="Other", potential_priority="high"),
        make_job(job_key="applied", company="Other", review_status="applied", application_status="applied"),
        make_job(job_key="closed", company="Other", status="confirmed_closed"),
    ]

    plan = build_priority_lifecycle_plan(jobs, target_keys={"acme"}, now="2026-06-27T12:00:00Z")

    assert [row["job_key"] for row in plan] == ["applied", "high", "target", "low", "closed"]
    assert plan[0]["cadence_reason"] == "interested_or_applied_daily"
    assert plan[2]["cadence_reason"] == "target_company_daily"
