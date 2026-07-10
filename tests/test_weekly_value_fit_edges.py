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
    return build_weekly_records(jobs, [], as_of="2026-07-09", backfill_weeks=1)[0]


def test_provisional_high_score_is_not_counted_as_verified_strong_fit():
    record = current_record(
        [
            make_job(
                score_status="provisional",
                verified_total_score=None,
                verified_alert_tier="",
                enrichment_status="pending",
            )
        ]
    )

    assert record["Strong Fit Jobs"] == 0


def test_hard_excluded_director_is_not_counted_as_stretch_fit():
    record = current_record(
        [
            make_job(
                title="Director, Commercial Strategy",
                role_level="Director",
                score_status="excluded",
                alert_tier="exclude",
                verified_alert_tier="exclude",
                score_explanation="seniority_fit=stretch; hard_exclude=true",
            )
        ]
    )

    assert record["Stretch Fit Jobs"] == 0
    assert record["Auto-Rejected Jobs"] == 1
