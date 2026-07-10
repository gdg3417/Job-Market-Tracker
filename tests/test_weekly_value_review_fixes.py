from __future__ import annotations

from src.models import JobPosting
from src.weekly_value import (
    WEEKLY_VALUE_HEADERS,
    WEEKLY_VALUE_SHEET,
    apply_weekly_value,
    build_weekly_records,
)


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-06",
        "status": "open",
        "review_status": "not_reviewed",
        "application_status": "",
        "role_level": "Director",
        "total_score": 70,
        "alert_tier": "track_only",
        "score_status": "verified",
        "verified_total_score": 70,
        "verified_alert_tier": "track_only",
        "score_explanation": "seniority_fit=stretch; seniority_reason=stretch_seniority_director",
    }
    values.update(overrides)
    return JobPosting(**values)


class WorksheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self):
        self.id = 4402
        self.update_calls = []

    def clear(self):
        return None

    def update(self, *, range_name, values, value_input_option):
        self.update_calls.append((range_name, values, value_input_option))


class FakeWorkbook:
    def __init__(self):
        self.batch_update_calls = []

    def batch_update(self, request):
        self.batch_update_calls.append(request)


class FakeSheetClient:
    def __init__(self, *, jobs=None, existing_rows=None, missing_rejected=False):
        self.jobs = jobs or []
        self.existing_rows = existing_rows
        self.missing_rejected = missing_rejected
        self.worksheet = FakeWorksheet()
        self.workbook = FakeWorkbook()

    def read_jobs_with_row_numbers(self):
        return [(index + 2, job) for index, job in enumerate(self.jobs)]

    def read_records(self, worksheet_name):
        if worksheet_name == "Rejected_Jobs":
            if self.missing_rejected:
                raise WorksheetNotFound()
            return []
        if worksheet_name == WEEKLY_VALUE_SHEET:
            if self.existing_rows is None:
                raise WorksheetNotFound()
            return self.existing_rows
        raise AssertionError(worksheet_name)

    def ensure_worksheet(self, worksheet_name, *, rows=1000, cols=26):
        assert worksheet_name == WEEKLY_VALUE_SHEET
        assert cols == len(WEEKLY_VALUE_HEADERS)
        return self.worksheet


def test_missing_rejected_jobs_sheet_is_treated_as_empty_input():
    client = FakeSheetClient(jobs=[make_job()], missing_rejected=True)

    result = apply_weekly_value(client, as_of="2026-07-09", backfill_weeks=1)

    assert result.rejected_rows_read == 0
    assert result.weeks_written == 2
    assert client.worksheet.update_calls


def test_production_refresh_recalculates_prior_week_when_backfill_is_one():
    client = FakeSheetClient(
        existing_rows=[
            {
                "Week Start": "2026-06-29",
                "Week End": "2026-07-05",
                "Jobs Added": 99,
                "Jobs Still Not Reviewed Yet": 5,
            }
        ]
    )

    apply_weekly_value(client, as_of="2026-07-09", backfill_weeks=1)

    written_values = client.worksheet.update_calls[0][1]
    records = [dict(zip(WEEKLY_VALUE_HEADERS, row)) for row in written_values[1:]]
    by_week = {record["Week Start"]: record for record in records}
    assert by_week["2026-06-29"]["Jobs Added"] == 0


def test_low_signal_director_is_not_counted_as_stretch_fit():
    record = build_weekly_records(
        [
            make_job(
                total_score=40,
                verified_total_score=40,
                alert_tier="ignore",
                verified_alert_tier="ignore",
                potential_priority="low",
            )
        ],
        [],
        as_of="2026-07-09",
        backfill_weeks=1,
    )[0]

    assert record["Stretch Fit Jobs"] == 0
    assert record["Signal Quality"] == 0.0


def test_high_potential_director_remains_a_stretch_fit_before_verification():
    record = build_weekly_records(
        [
            make_job(
                total_score=30,
                verified_total_score=None,
                verified_alert_tier="",
                score_status="provisional",
                alert_tier="ignore",
                potential_priority="high",
                enrichment_status="pending",
            )
        ],
        [],
        as_of="2026-07-09",
        backfill_weeks=1,
    )[0]

    assert record["Stretch Fit Jobs"] == 1
