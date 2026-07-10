from __future__ import annotations

from datetime import date, datetime

from src.models import JobPosting
from src.weekly_value import build_weekly_records
from src.weekly_value_sheet_dates import (
    JOB_DATE_FIELDS,
    normalize_record_dates,
    normalize_sheet_date,
)


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-06-29",
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


def test_normalize_sheet_date_accepts_google_sheets_display_formats():
    assert normalize_sheet_date("6/30/26") == "2026-06-30"
    assert normalize_sheet_date("06/30/2026") == "2026-06-30"
    assert normalize_sheet_date("6/30/26 12:15:00") == "2026-06-30"
    assert normalize_sheet_date("2026-06-30T12:15:00Z") == "2026-06-30"


def test_normalize_sheet_date_accepts_date_objects_and_serials():
    assert normalize_sheet_date(date(2026, 6, 30)) == "2026-06-30"
    assert normalize_sheet_date(datetime(2026, 6, 30, 8, 15)) == "2026-06-30"
    assert normalize_sheet_date(46203) == "2026-06-30"


def test_unknown_or_blank_sheet_dates_remain_safe():
    assert normalize_sheet_date("") == ""
    assert normalize_sheet_date(None) is None
    assert normalize_sheet_date("not-a-date") == "not-a-date"


def test_normalize_record_dates_only_updates_requested_fields():
    record = {
        "reviewed_date": "6/30/26",
        "application_date": "7/1/26",
        "review_notes": "Keep 6/30/26 wording unchanged",
    }

    normalized = normalize_record_dates(record, JOB_DATE_FIELDS)

    assert normalized["reviewed_date"] == "2026-06-30"
    assert normalized["application_date"] == "2026-07-01"
    assert normalized["review_notes"] == record["review_notes"]
    assert record["reviewed_date"] == "6/30/26"


def test_sheet_formatted_review_date_restores_weekly_review_and_dismissal_metrics():
    job = make_job(
        review_status="dismissed",
        interest_decision="not_interested",
        reviewed_date=normalize_sheet_date("6/30/26"),
    )

    record = build_weekly_records(
        [job],
        [],
        as_of="2026-07-04",
        backfill_weeks=1,
    )[0]

    assert record["Jobs Reviewed"] == 1
    assert record["Jobs Dismissed"] == 1
    assert record["Review Completion Rate"] == 1.0
    assert record["Dismissal Rate"] == 1.0


def test_sheet_formatted_application_date_restores_application_metrics():
    job = make_job(
        review_status="applied",
        application_status="applied",
        reviewed_date=normalize_sheet_date("6/30/26"),
        application_date=normalize_sheet_date("7/1/26"),
    )

    record = build_weekly_records(
        [job],
        [],
        as_of="2026-07-04",
        backfill_weeks=1,
    )[0]

    assert record["Jobs Reviewed"] == 1
    assert record["Jobs Applied"] == 1
    assert record["Jobs Moved to Active Status"] == 1
