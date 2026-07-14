from __future__ import annotations

from datetime import date, datetime

from src.follow_up import evaluate_follow_up
from src.models import JobPosting
from src.sheet_dates import (
    JOB_DATE_FIELDS,
    normalize_job,
    normalize_record_dates,
    normalize_sheet_date,
)


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-01",
        "status": "open",
        "review_status": "reviewing",
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


def test_shared_normalizer_supports_all_required_sheet_date_forms():
    assert normalize_sheet_date("2026-07-04") == "2026-07-04"
    assert normalize_sheet_date("7/4/26") == "2026-07-04"
    assert normalize_sheet_date("07/04/2026") == "2026-07-04"
    assert normalize_sheet_date(date(2026, 7, 4)) == "2026-07-04"
    assert normalize_sheet_date(datetime(2026, 7, 4, 11, 30)) == "2026-07-04"
    assert normalize_sheet_date(46207) == "2026-07-04"
    assert normalize_sheet_date("") == ""
    assert normalize_sheet_date(None) is None
    assert normalize_sheet_date("unknown-date") == "unknown-date"


def test_record_normalization_is_non_destructive_outside_date_fields():
    record = {
        "reviewed_date": "7/1/26",
        "application_date": "7/2/26",
        "review_notes": "Keep 7/1/26 in the note",
    }
    normalized = normalize_record_dates(record, JOB_DATE_FIELDS)
    assert normalized["reviewed_date"] == "2026-07-01"
    assert normalized["application_date"] == "2026-07-02"
    assert normalized["review_notes"] == record["review_notes"]
    assert record["reviewed_date"] == "7/1/26"


def test_follow_up_aging_uses_sheet_formatted_reviewed_date():
    job = make_job(reviewed_date="7/1/26")
    result = evaluate_follow_up(job, as_of="2026-07-08")
    assert result.last_status_update_date == "2026-07-01"
    assert result.days_since_status_update == 7
    assert result.follow_up_due is True


def test_application_aging_uses_sheet_formatted_application_date():
    job = make_job(
        review_status="applied",
        application_status="applied",
        application_date="7/2/26",
    )
    result = evaluate_follow_up(job, as_of="2026-07-09")
    assert result.last_status_update_date == "2026-07-02"
    assert result.days_since_status_update == 7
    assert result.follow_up_due is True


def test_explicit_due_date_overrides_a_later_second_schedule_date():
    job = make_job(
        review_status="applied",
        application_status="applied",
        application_date="7/7/26",
        next_action_date="7/8/26",
        follow_up_date="7/20/26",
    )
    result = evaluate_follow_up(job, as_of="2026-07-09")
    assert result.follow_up_due is True
    assert "2026-07-08" in result.follow_up_reason


def test_normalize_job_returns_a_copy_with_iso_dates():
    job = make_job(reviewed_date="7/1/26")
    normalized = normalize_job(job)
    assert normalized.reviewed_date == "2026-07-01"
    assert job.reviewed_date == "7/1/26"
