from __future__ import annotations

from pathlib import Path

from src.models import JobPosting
from src.weekly_context import (
    CORE_METRICS,
    DASHBOARD_ONLY_METRICS,
    NOISE_METRICS,
    WEEKLY_CONTEXT_HEADERS,
    WEEKLY_CONTEXT_SHEET,
    WeeklyDigestConfig,
    apply_weekly_context,
    build_weekly_context_rows,
    load_weekly_digest_config,
)


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


def weekly_record(**overrides):
    values = {
        "Week Start": "2026-06-29",
        "Week End": "2026-07-05",
        "Jobs Added": 20,
        "Jobs Reviewed": 8,
        "Jobs Dismissed": 4,
        "Jobs Applied": 2,
        "Jobs Moved to Active Status": 3,
        "Jobs Still Not Reviewed Yet": 12,
        "Follow-ups Due": 2,
        "Outstanding Active Roles": 5,
        "Strong Fit Jobs": 3,
        "Stretch Fit Jobs": 1,
        "Auto-Rejected Jobs": 11,
        "Blocked Company Rejects": 4,
        "Too-Senior Rejects or Penalties": 6,
        "Review Completion Rate": 0.4,
        "Actionable Conversion Rate": 0.25,
        "Dismissal Rate": 0.5,
        "Backlog Change": -3,
        "Signal Quality": 0.2,
        "Noise Removed": 0.35,
        "Notes": "Historical metrics reconstructed from durable date fields.",
    }
    values.update(overrides)
    return values


def build_rows(jobs=None, *, config=None, records=None):
    return build_weekly_context_rows(
        [(index + 2, job) for index, job in enumerate(jobs or [])],
        records if records is not None else [weekly_record()],
        as_of="2026-07-06",
        config=config,
    )


def test_formatted_google_sheets_week_dates_are_normalized_before_selection():
    record = weekly_record(**{"Week Start": "6/29/26", "Week End": "7/5/26"})
    rows = build_rows(records=[record])
    period = next(row for row in rows if row["item_type"] == "period")
    jobs_added = next(row for row in rows if row["label"] == "Jobs Added")

    assert period["week_start"] == "2026-06-29"
    assert period["week_end"] == "2026-07-05"
    assert jobs_added["value"] == 20


def test_core_weekly_metrics_are_included_without_full_dashboard_metrics():
    rows = build_rows()
    metric_labels = {row["label"] for row in rows if row["item_type"] == "metric"}

    assert set(CORE_METRICS).issubset(metric_labels)
    assert set(NOISE_METRICS).issubset(metric_labels)
    assert not set(DASHBOARD_ONLY_METRICS).intersection(metric_labels)


def test_optional_dashboard_metric_can_be_enabled_without_scoring_changes():
    config = WeeklyDigestConfig(include_optional_metrics=("Review Completion Rate", "Backlog Change"))
    rows = build_rows(config=config)
    metric_labels = {row["label"] for row in rows if row["item_type"] == "metric"}

    assert "Review Completion Rate" in metric_labels
    assert "Backlog Change" in metric_labels
    assert "Dismissal Rate" not in metric_labels


def test_follow_up_due_items_are_included_with_reason_and_row_reference():
    job = make_job(
        review_status="applied",
        application_status="applied",
        application_date="2026-06-20",
        reviewed_date="2026-06-20",
    )
    rows = build_rows([job])
    follow_ups = [row for row in rows if row["item_type"] == "follow_up"]

    assert len(follow_ups) == 1
    assert follow_ups[0]["company"] == "Acme Industrial"
    assert "has not been updated" in follow_ups[0]["reason"]
    assert follow_ups[0]["source_sheet"] == "Jobs"
    assert follow_ups[0]["source_row"] == 2


def test_new_strong_and_stretch_roles_are_both_visible():
    jobs = [
        make_job(job_key="strong", title="Senior Manager, Revenue Strategy"),
        make_job(
            job_key="stretch",
            title="Director, Commercial Strategy",
            role_level="Director",
            score_explanation="seniority_fit=stretch; seniority_reason=stretch_seniority_director",
        ),
    ]
    rows = build_rows(jobs)
    matches = [row for row in rows if row["item_type"] == "match"]

    assert {row["fit_type"] for row in matches} == {"Strong Fit", "Stretch Fit"}
    assert {row["company"] for row in matches} == {"Acme Industrial"}


def test_top_item_limits_keep_digest_concise():
    review_jobs = [
        make_job(
            job_key=f"review-{index}",
            title=f"Manager, Strategy {index}",
            first_seen_date="2026-06-01",
            total_score=60,
            verified_total_score=60,
            alert_tier="track_only",
            verified_alert_tier="track_only",
            potential_priority="medium",
            potential_priority_score=60 - index,
        )
        for index in range(8)
    ]
    follow_up_jobs = [
        make_job(
            job_key=f"follow-{index}",
            title=f"Senior Manager, Operations {index}",
            first_seen_date="2026-06-01",
            review_status="applied",
            application_status="applied",
            reviewed_date="2026-06-20",
            application_date="2026-06-20",
        )
        for index in range(8)
    ]
    config = WeeklyDigestConfig(top_review_limit=5, top_follow_up_limit=5, top_new_match_limit=5)
    rows = build_rows([*review_jobs, *follow_up_jobs], config=config)

    assert sum(1 for row in rows if row["item_type"] == "review") == 5
    assert sum(1 for row in rows if row["item_type"] == "follow_up") == 5


def test_empty_week_produces_clean_zero_metric_contract():
    rows = build_rows(records=[])
    period = next(row for row in rows if row["item_type"] == "period")
    metrics = [row for row in rows if row["item_type"] == "metric"]

    assert period["week_start"] == "2026-06-29"
    assert period["week_end"] == "2026-07-05"
    assert metrics
    assert all(row["value"] == 0 for row in metrics)
    assert not any(row["item_type"] in {"review", "match", "follow_up"} for row in rows)


def test_missing_optional_job_fields_do_not_break_render_contract():
    job = make_job(
        canonical_url="",
        score_explanation="",
        potential_priority="",
        potential_priority_score=None,
        verified_total_score=76,
    )
    rows = build_rows([job])
    match = next(row for row in rows if row["item_type"] == "match")

    assert match["canonical_url"] == ""
    assert match["reason"] == "Strong Fit"


def test_yaml_configuration_is_external_to_scoring_logic(tmp_path):
    path = tmp_path / "weekly_digest.yml"
    path.write_text(
        """
weekly_digest:
  summary_week: latest_available
  top_review_limit: 3
  top_follow_up_limit: 4
  top_new_match_limit: 2
  include_dashboard_only_metrics: false
  include_optional_metrics:
    - Review Completion Rate
    - Not A Real Metric
""".strip(),
        encoding="utf-8",
    )

    config = load_weekly_digest_config(path)

    assert config.summary_week == "latest_available"
    assert config.top_review_limit == 3
    assert config.top_follow_up_limit == 4
    assert config.top_new_match_limit == 2
    assert config.include_optional_metrics == ("Review Completion Rate",)


class FakeWorksheet:
    def __init__(self):
        self.id = 4501
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

    def read_records(self, worksheet_name):
        assert worksheet_name == "Weekly_Value"
        return [weekly_record()]

    def ensure_worksheet(self, worksheet_name, *, rows=1000, cols=26):
        assert worksheet_name == WEEKLY_CONTEXT_SHEET
        assert cols == len(WEEKLY_CONTEXT_HEADERS)
        return self.worksheet


def test_apply_weekly_context_writes_gray_read_only_surface_without_merges():
    client = FakeSheetClient([make_job()])

    result = apply_weekly_context(client, as_of="2026-07-06")

    assert result.jobs_read == 1
    assert client.worksheet.clear_calls == 1
    assert client.worksheet.update_calls[0][1][0] == WEEKLY_CONTEXT_HEADERS
    requests = client.workbook.batch_update_calls[0]["requests"]
    assert any("setBasicFilter" in request for request in requests)
    assert not any("mergeCells" in request for request in requests)
    header_format = requests[2]["repeatCell"]["cell"]["userEnteredFormat"]
    assert header_format["backgroundColor"] == {"red": 0.72, "green": 0.72, "blue": 0.72}
    body_format = requests[3]["repeatCell"]["cell"]["userEnteredFormat"]
    assert body_format["backgroundColor"] == {"red": 1, "green": 1, "blue": 1}


def test_apps_script_uses_weekly_context_with_legacy_fallback():
    context_script = Path("apps_script/weekly_context_digest.gs").read_text(encoding="utf-8")

    assert "Weekly_Context" in context_script
    assert "sendWeeklyContextOrLegacy_" in context_script
    assert "sendWeeklyDigest_(options)" in context_script
    assert "return false" in context_script
    assert "Weekly Tracker Metrics" in context_script
    assert "Backlog and Follow-up" in context_script
