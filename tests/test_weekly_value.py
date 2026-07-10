from __future__ import annotations

from src.models import JobPosting
from src.weekly_value import (
    WEEKLY_VALUE_HEADERS,
    WEEKLY_VALUE_SHEET,
    apply_weekly_value,
    build_weekly_records,
    merge_weekly_records,
)


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


def current_record(jobs, rejected_rows=None):
    records = build_weekly_records(
        jobs,
        rejected_rows or [],
        as_of="2026-07-09",
        backfill_weeks=1,
    )
    return records[0]


def test_weekly_jobs_added_reviewed_dismissed_and_applied_calculations():
    jobs = [
        make_job(job_key="reviewed", reviewed_date="2026-07-07", review_status="interested"),
        make_job(
            job_key="dismissed",
            reviewed_date="2026-07-08",
            review_status="dismissed",
            total_score=20,
            verified_total_score=20,
            alert_tier="ignore",
            verified_alert_tier="ignore",
        ),
        make_job(
            job_key="applied",
            reviewed_date="2026-07-07",
            review_status="applied",
            application_status="applied",
            application_date="2026-07-09",
        ),
        make_job(job_key="unreviewed"),
    ]

    record = current_record(jobs)

    assert record["Jobs Added"] == 4
    assert record["Jobs Reviewed"] == 3
    assert record["Jobs Dismissed"] == 1
    assert record["Jobs Applied"] == 1
    assert record["Review Completion Rate"] == 0.75
    assert record["Dismissal Rate"] == 0.3333


def test_current_not_reviewed_backlog_excludes_auto_rejected_jobs():
    jobs = [
        make_job(job_key="open-backlog", total_score=50, verified_total_score=50, alert_tier="track_only", verified_alert_tier="track_only"),
        make_job(job_key="reviewed", reviewed_date="2026-07-07", review_status="interested"),
        make_job(
            job_key="blocked",
            company="Deloitte",
            score_status="excluded",
            alert_tier="exclude",
            verified_alert_tier="exclude",
            score_explanation="company_exclusion=true; company_exclusion_reason=blocked_company; hard_exclude=true",
        ),
    ]

    assert current_record(jobs)["Jobs Still Not Reviewed Yet"] == 1


def test_follow_ups_due_and_outstanding_active_roles_use_sprint_43_logic():
    jobs = [
        make_job(
            job_key="due",
            first_seen_date="2026-06-20",
            review_status="applied",
            application_status="applied",
            application_date="2026-06-30",
        ),
        make_job(
            job_key="not-due",
            review_status="applied",
            application_status="applied",
            application_date="2026-07-07",
        ),
        make_job(job_key="inactive"),
    ]

    record = current_record(jobs)

    assert record["Outstanding Active Roles"] == 2
    assert record["Follow-ups Due"] == 1
    assert record["Jobs Moved to Active Status"] == 1


def test_strong_and_stretch_fit_counts_are_separate():
    jobs = [
        make_job(job_key="strong-manager"),
        make_job(
            job_key="stretch-director",
            title="Director, Commercial Strategy",
            role_level="Director",
            score_explanation="seniority_fit=stretch; seniority_reason=stretch_seniority_director",
        ),
    ]

    record = current_record(jobs)

    assert record["Strong Fit Jobs"] == 1
    assert record["Stretch Fit Jobs"] == 1
    assert record["Signal Quality"] == 1.0


def test_blocked_company_and_too_senior_counts_are_auditable():
    jobs = [
        make_job(
            job_key="blocked",
            company="Deloitte",
            score_status="excluded",
            alert_tier="exclude",
            verified_alert_tier="exclude",
            score_explanation="company_exclusion=true; company_exclusion_reason=blocked_company; hard_exclude=true",
        ),
        make_job(
            job_key="vp",
            title="VP, Commercial Strategy",
            role_level="VP",
            total_score=25,
            verified_total_score=25,
            alert_tier="ignore",
            verified_alert_tier="ignore",
            score_explanation="seniority_reason=likely_too_senior_vp",
        ),
    ]
    rejected_rows = [
        {
            "created_at": "2026-07-08T12:00:00Z",
            "rejection_reason": "blocked company",
            "company": "Gartner",
        }
    ]

    record = current_record(jobs, rejected_rows)

    assert record["Auto-Rejected Jobs"] == 2
    assert record["Blocked Company Rejects"] == 2
    assert record["Too-Senior Rejects or Penalties"] == 1
    assert record["Noise Removed"] == 0.6667


def test_empty_week_and_missing_dates_are_handled_safely():
    record = current_record(
        [make_job(job_key="missing", first_seen_date="")],
        [{"rejection_reason": "missing date"}],
    )

    assert record["Jobs Added"] == 0
    assert record["Review Completion Rate"] == 0.0
    assert record["Signal Quality"] == 0.0
    assert "lacked first_seen_date" in record["Notes"]
    assert "lacked a usable date" in record["Notes"]


def test_prior_week_history_is_preserved_while_current_and_previous_refresh():
    generated = build_weekly_records([], [], as_of="2026-07-09", backfill_weeks=4)
    existing = [
        {
            "Week Start": "2026-06-15",
            "Week End": "2026-06-21",
            "Jobs Added": 99,
            "Jobs Still Not Reviewed Yet": 10,
        },
        {
            "Week Start": "2026-06-29",
            "Week End": "2026-07-05",
            "Jobs Added": 88,
            "Jobs Still Not Reviewed Yet": 8,
        },
    ]

    merged = merge_weekly_records(existing, generated, as_of="2026-07-09")
    by_week = {record["Week Start"]: record for record in merged}

    assert by_week["2026-06-15"]["Jobs Added"] == 99
    assert by_week["2026-06-29"]["Jobs Added"] == 0
    assert merged[0]["Week Start"] == "2026-07-06"


class WorksheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self):
        self.id = 4401
        self.clear_calls = 0
        self.update_calls = []

    def clear(self):
        self.clear_calls += 1

    def update(self, *, range_name, values, value_input_option):
        self.update_calls.append((range_name, values, value_input_option))


class FakeWorkbook:
    def __init__(self):
        self.batch_update_calls = []

    def batch_update(self, request):
        self.batch_update_calls.append(request)


class FakeSheetClient:
    def __init__(self, jobs, rejected_rows=None, existing_rows=None):
        self.jobs = jobs
        self.rejected_rows = rejected_rows or []
        self.existing_rows = existing_rows
        self.worksheet = FakeWorksheet()
        self.workbook = FakeWorkbook()

    def read_jobs_with_row_numbers(self):
        return [(index + 2, job) for index, job in enumerate(self.jobs)]

    def read_records(self, worksheet_name):
        if worksheet_name == "Rejected_Jobs":
            return self.rejected_rows
        if worksheet_name == WEEKLY_VALUE_SHEET:
            if self.existing_rows is None:
                raise WorksheetNotFound()
            return self.existing_rows
        raise AssertionError(worksheet_name)

    def ensure_worksheet(self, worksheet_name, *, rows=1000, cols=26):
        assert worksheet_name == WEEKLY_VALUE_SHEET
        assert cols == len(WEEKLY_VALUE_HEADERS)
        return self.worksheet


def test_apply_weekly_value_writes_filterable_gray_system_managed_surface():
    client = FakeSheetClient([make_job()])

    result = apply_weekly_value(client, as_of="2026-07-09", backfill_weeks=2)

    assert result.jobs_read == 1
    assert result.weeks_written == 2
    assert client.worksheet.clear_calls == 1
    assert client.worksheet.update_calls[0][1][0] == WEEKLY_VALUE_HEADERS
    requests = client.workbook.batch_update_calls[0]["requests"]
    assert any("setBasicFilter" in request for request in requests)
    assert not any("mergeCells" in request for request in requests)
    freeze = requests[0]["updateSheetProperties"]["properties"]["gridProperties"]
    assert freeze == {"frozenRowCount": 1, "frozenColumnCount": 2}
    header_format = requests[2]["repeatCell"]["cell"]["userEnteredFormat"]
    assert header_format["backgroundColor"] == {"red": 0.72, "green": 0.72, "blue": 0.72}
