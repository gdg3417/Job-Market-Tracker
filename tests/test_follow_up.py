from __future__ import annotations

from src.follow_up import (
    FOLLOW_UP_QUEUE_HEADERS,
    FOLLOW_UP_QUEUE_SHEET,
    apply_follow_up_queue,
    build_follow_up_rows,
    evaluate_follow_up,
)
from src.models import JobPosting


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "review_status": "not_reviewed",
        "application_status": "",
        "review_notes": "Preserve this user-entered note.",
    }
    values.update(overrides)
    return JobPosting(**values)


def row_as_record(row):
    return dict(zip(FOLLOW_UP_QUEUE_HEADERS, row))


def test_applied_role_older_than_seven_days_gets_follow_up_flag():
    result = evaluate_follow_up(
        make_job(application_status="applied", application_date="2026-06-01"),
        as_of="2026-06-08",
    )

    assert result.outstanding_status == "Applied"
    assert result.days_since_status_update == 7
    assert result.follow_up_due is True
    assert "threshold: 7" in result.follow_up_reason


def test_applied_role_updated_within_seven_days_does_not_get_follow_up_flag():
    result = evaluate_follow_up(
        make_job(application_status="applied", application_date="2026-06-03"),
        as_of="2026-06-08",
    )

    assert result.days_since_status_update == 5
    assert result.follow_up_due is False


def test_in_review_role_older_than_seven_days_gets_follow_up_flag():
    result = evaluate_follow_up(
        make_job(review_status="reviewing", reviewed_date="2026-06-01"),
        as_of="2026-06-08",
    )

    assert result.outstanding_status == "In Review"
    assert result.follow_up_due is True


def test_dismissed_role_never_gets_follow_up_flag():
    result = evaluate_follow_up(
        make_job(review_status="dismissed", reviewed_date="2026-05-01"),
        as_of="2026-06-08",
    )

    assert result.outstanding_status_flag is False
    assert result.follow_up_due is False


def test_not_reviewed_role_does_not_get_follow_up_flag():
    result = evaluate_follow_up(make_job(review_status="not_reviewed"), as_of="2026-06-08")

    assert result.outstanding_status_flag is False
    assert result.follow_up_due is False


def test_waiting_on_response_gets_follow_up_flag_after_threshold():
    result = evaluate_follow_up(
        make_job(
            application_status="interviewing",
            interview_stage="Waiting on response after final interview",
            last_application_update="2026-06-01",
        ),
        as_of="2026-06-07",
    )

    assert result.outstanding_status == "Waiting on Response"
    assert result.days_since_status_update == 6
    assert result.follow_up_due is True


def test_recruiter_hiring_manager_interview_case_and_offer_thresholds():
    cases = [
        ("Recruiter Screen", "Recruiter Screen", 4),
        ("Hiring Manager Screen", "Hiring Manager Screen", 4),
        ("Final round interview", "Interviewing", 4),
        ("Take-home case study", "Take-home / Case", 4),
        ("Offer negotiation", "Offer / Negotiation", 2),
    ]
    for stage, expected_status, elapsed_days in cases:
        result = evaluate_follow_up(
            make_job(
                application_status="interviewing",
                interview_stage=stage,
                last_application_update="2026-06-01",
            ),
            as_of=f"2026-06-{1 + elapsed_days:02d}",
        )
        assert result.outstanding_status == expected_status
        assert result.follow_up_due is True


def test_missing_status_update_date_is_handled_safely_and_flagged_for_review():
    result = evaluate_follow_up(make_job(application_status="applied"), as_of="2026-06-08")

    assert result.outstanding_status_flag is True
    assert result.last_status_update_date == ""
    assert result.days_since_status_update is None
    assert result.follow_up_due is True
    assert "missing a status update date" in result.follow_up_reason


def test_last_application_update_takes_precedence_over_older_application_date():
    result = evaluate_follow_up(
        make_job(
            application_status="applied",
            application_date="2026-05-01",
            last_application_update="2026-06-05",
        ),
        as_of="2026-06-08",
    )

    assert result.last_status_update_date == "2026-06-05"
    assert result.days_since_status_update == 3
    assert result.follow_up_due is False


def test_explicit_next_action_date_triggers_follow_up_even_before_threshold():
    result = evaluate_follow_up(
        make_job(
            application_status="applied",
            application_date="2026-06-06",
            next_action_date="2026-06-08",
        ),
        as_of="2026-06-08",
    )

    assert result.days_since_status_update == 2
    assert result.follow_up_due is True
    assert "Scheduled follow-up date" in result.follow_up_reason


def test_existing_user_notes_are_preserved_in_queue_output():
    job = make_job(
        application_status="applied",
        application_date="2026-06-01",
        review_notes="Do not overwrite this note.",
    )

    record = row_as_record(build_follow_up_rows([job], as_of="2026-06-08")[0])

    assert record["review_notes"] == "Do not overwrite this note."
    assert record["follow_up_due"] is True


def test_follow_up_rows_are_filterable_and_due_rows_sort_first():
    due = make_job(
        job_key="due",
        company="Beta",
        application_status="applied",
        application_date="2026-06-01",
    )
    not_due = make_job(
        job_key="not-due",
        company="Alpha",
        application_status="applied",
        application_date="2026-06-07",
    )

    records = [row_as_record(row) for row in build_follow_up_rows([not_due, due], as_of="2026-06-08")]

    assert records[0]["job_key"] == "due"
    assert records[0]["follow_up_due"] is True
    assert records[1]["follow_up_due"] is False


class FakeWorksheet:
    def __init__(self):
        self.id = 1001
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
    def __init__(self, jobs):
        self.jobs = jobs
        self.worksheet = FakeWorksheet()
        self.workbook = FakeWorkbook()

    def read_jobs_with_row_numbers(self):
        return [(index + 2, job) for index, job in enumerate(self.jobs)]

    def ensure_worksheet(self, worksheet_name, *, rows=1000, cols=26):
        assert worksheet_name == FOLLOW_UP_QUEUE_SHEET
        assert cols == len(FOLLOW_UP_QUEUE_HEADERS)
        return self.worksheet


def test_apply_follow_up_queue_writes_generated_surface_without_updating_jobs():
    job = make_job(application_status="applied", application_date="2026-06-01")
    client = FakeSheetClient([job])

    result = apply_follow_up_queue(client, as_of="2026-06-08")

    assert result.jobs_read == 1
    assert result.outstanding_rows == 1
    assert result.follow_up_due_rows == 1
    assert client.worksheet.clear_calls == 1
    assert client.worksheet.update_calls[0][1][0] == FOLLOW_UP_QUEUE_HEADERS
    requests = client.workbook.batch_update_calls[0]["requests"]
    assert any("setBasicFilter" in request for request in requests)
    freeze = requests[0]["updateSheetProperties"]["properties"]["gridProperties"]
    assert freeze == {"frozenRowCount": 1, "frozenColumnCount": 3}
