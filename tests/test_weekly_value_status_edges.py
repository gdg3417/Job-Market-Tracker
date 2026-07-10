from src.models import JobPosting
from src.weekly_value import build_weekly_records


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-06",
        "status": "open",
        "review_status": "not_reviewed",
        "application_status": "",
        "role_level": "Senior Manager",
        "total_score": 80,
        "alert_tier": "strong_fit",
        "score_status": "verified",
        "verified_total_score": 80,
        "verified_alert_tier": "strong_fit",
    }
    values.update(overrides)
    return JobPosting(**values)


def current_record(jobs):
    return build_weekly_records(
        jobs,
        [],
        as_of="2026-07-09",
        backfill_weeks=1,
    )[0]


def test_recruiter_rejection_is_not_counted_as_user_dismissal():
    record = current_record(
        [
            make_job(
                reviewed_date="2026-07-07",
                review_status="rejected",
                application_status="rejected",
                application_date="2026-07-06",
            )
        ]
    )

    assert record["Jobs Reviewed"] == 1
    assert record["Jobs Dismissed"] == 0


def test_application_is_counted_as_active_movement_even_if_closed_later_in_week():
    record = current_record(
        [
            make_job(
                status="confirmed_closed",
                closed_date="2026-07-09",
                reviewed_date="2026-07-07",
                review_status="rejected",
                application_status="rejected",
                application_date="2026-07-07",
                last_application_update="2026-07-09",
            )
        ]
    )

    assert record["Jobs Applied"] == 1
    assert record["Jobs Moved to Active Status"] == 1
    assert record["Outstanding Active Roles"] == 0
