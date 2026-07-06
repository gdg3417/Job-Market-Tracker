from __future__ import annotations

from src.dashboard import build_dashboard_values, build_digest_values
from src.models import JobPosting
from src.review_queue import (
    FILTERABLE_HEADERS,
    REVIEW_QUEUE_HEADERS,
    REVIEW_QUEUE_SHEET,
    apply_review_queue,
    build_review_queue_rows,
    build_review_queue_values,
    should_include_review_queue_job,
    sort_review_queue_jobs,
)


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Operations",
        "role_level": "Director",
        "location": "Plano, TX Hybrid",
        "canonical_url": "https://example.com/job",
        "source_primary": "gmail",
        "source_job_id": "abc-123",
        "status": "open",
        "potential_priority": "high",
        "potential_priority_score": 80,
        "score_status": "partially_verified",
        "evidence_completeness_score": 35,
        "enrichment_status": "partial",
        "enrichment_match_confidence": 70,
        "work_model": "hybrid",
        "base_salary_min": 170000,
        "base_salary_max": 220000,
        "compensation_source_type": "employer_posted",
        "commute_bucket": "15_to_30_minutes",
        "review_status": "not_reviewed",
        "move_value_classification": "potentially_better",
        "move_value_notes": "Strong scope, evidence incomplete.",
        "score_explanation": "seniority_fit=stretch; seniority_reason=stretch_seniority_director",
    }
    values.update(overrides)
    return JobPosting(**values)


class _FakeWorksheet:
    def __init__(self, title: str, worksheet_id: int) -> None:
        self.title = title
        self.id = worksheet_id
        self.clear_calls = 0
        self.update_calls: list[tuple[str, list[list[object]], str]] = []

    def clear(self) -> None:
        self.clear_calls += 1

    def update(self, *, range_name: str, values: list[list[object]], value_input_option: str) -> None:
        self.update_calls.append((range_name, values, value_input_option))


class _FakeWorkbook:
    def __init__(self) -> None:
        self.batch_update_calls: list[dict] = []

    def batch_update(self, request: dict) -> None:
        self.batch_update_calls.append(request)


class _FakeSheetClient:
    def __init__(self, jobs: list[JobPosting]) -> None:
        self.jobs = jobs
        self.workbook = _FakeWorkbook()
        self.review_worksheet = _FakeWorksheet(REVIEW_QUEUE_SHEET, 1001)
        self.jobs_worksheet = _FakeWorksheet("Jobs", 1002)
        self.updated_jobs: list[tuple[int, JobPosting]] = []

    def read_jobs_with_row_numbers(self) -> list[tuple[int, JobPosting]]:
        return [(index + 2, job) for index, job in enumerate(self.jobs)]

    def ensure_worksheet(self, worksheet_name: str, *, rows: int = 1000, cols: int = 26) -> _FakeWorksheet:
        assert worksheet_name == REVIEW_QUEUE_SHEET
        assert rows >= len(self.jobs) + 1
        assert cols == len(REVIEW_QUEUE_HEADERS)
        return self.review_worksheet

    def get_worksheet(self, worksheet_name: str) -> _FakeWorksheet:
        assert worksheet_name == "Jobs"
        return self.jobs_worksheet

    def worksheet_headers(self, worksheet_name: str) -> list[str]:
        assert worksheet_name == "Jobs"
        return ["job_key", "company", "title", "location", "review_status", "manual_priority"]

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.updated_jobs.append((row_number, job))


def row_as_record(row: list[object]) -> dict[str, object]:
    return dict(zip(REVIEW_QUEUE_HEADERS, row))


def test_review_queue_column_order_keeps_identity_seniority_and_review_fields_together():
    assert REVIEW_QUEUE_HEADERS[:8] == [
        "job_key",
        "company",
        "title",
        "role_level",
        "seniority_fit",
        "seniority_reason",
        "location",
        "canonical_url",
    ]
    assert REVIEW_QUEUE_HEADERS[8:15] == [
        "potential_priority",
        "potential_priority_score",
        "score_status",
        "evidence_completeness_score",
        "enrichment_status",
        "enrichment_match_confidence",
        "manual_authoritative_url",
    ]
    assert REVIEW_QUEUE_HEADERS[22:28] == [
        "review_status",
        "reviewed_date",
        "interest_decision",
        "manual_priority",
        "manual_fit_rating",
        "review_notes",
    ]
    assert REVIEW_QUEUE_HEADERS[-2:] == ["source_primary", "source_job_id"]


def test_required_review_queue_filter_fields_are_present():
    assert FILTERABLE_HEADERS <= set(REVIEW_QUEUE_HEADERS)


def test_review_queue_includes_high_potential_partial_evidence_jobs_and_seniority_fields():
    job = make_job(potential_priority="high", score_status="partially_verified", enrichment_status="partial")

    rows = build_review_queue_rows([job])

    assert len(rows) == 1
    record = row_as_record(rows[0])
    assert record["company"] == "Acme Industrial"
    assert record["score_status"] == "partially_verified"
    assert record["role_level"] == "Director"
    assert record["seniority_fit"] == "stretch"
    assert record["seniority_reason"] == "stretch_seniority_director"


def test_review_queue_includes_enrichment_failure_jobs():
    job = make_job(
        job_key="toyota-product",
        company="Toyota North America",
        title="National Manager, Product",
        role_level="Manager",
        potential_priority="high",
        score_status="provisional",
        enrichment_status="not_found",
        manual_authoritative_url="https://toyota.example/jobs/123",
        score_explanation="seniority_fit=target; seniority_reason=target_seniority_manager",
    )

    record = row_as_record(build_review_queue_rows([job])[0])

    assert record["company"] == "Toyota North America"
    assert record["enrichment_status"] == "not_found"
    assert record["manual_authoritative_url"] == "https://toyota.example/jobs/123"


def test_review_queue_includes_applied_and_interviewing_jobs():
    applied = make_job(job_key="topgolf", company="Topgolf", title="Sr Manager, Strategic Planning", role_level="Senior Manager", application_status="applied")
    interviewing = make_job(job_key="pipeline", application_status="interviewing", potential_priority="low", score_status="provisional")

    records = [row_as_record(row) for row in build_review_queue_rows([applied, interviewing])]

    assert {record["application_status"] for record in records} == {"applied", "interviewing"}


def test_review_queue_includes_dismissed_jobs_for_audit():
    dismissed = make_job(job_key="dismissed", review_status="dismissed", potential_priority="low", score_status="provisional")

    rows = build_review_queue_rows([dismissed])

    assert len(rows) == 1
    assert row_as_record(rows[0])["review_status"] == "dismissed"


def test_review_queue_excludes_terminal_noise_excluded_jobs_and_too_senior_jobs_without_review_state():
    closed = make_job(
        job_key="closed-low",
        status="confirmed_closed",
        closed_date="2026-06-01",
        potential_priority="low",
        score_status="provisional",
        enrichment_status="not_required",
        total_score=10,
    )
    excluded = make_job(
        job_key="hard-excluded",
        potential_priority="excluded",
        score_status="excluded",
        enrichment_status="not_required",
        total_score=0,
    )
    too_senior = make_job(
        job_key="vp-role",
        title="VP, Commercial Strategy",
        role_level="VP",
        potential_priority="medium",
        score_status="partially_verified",
        score_explanation="seniority_fit=too_senior; seniority_reason=likely_too_senior_vp",
    )

    assert should_include_review_queue_job(closed) is False
    assert should_include_review_queue_job(excluded) is False
    assert should_include_review_queue_job(too_senior) is False
    assert build_review_queue_rows([closed, excluded, too_senior]) == []


def test_review_queue_preserves_too_senior_jobs_with_manual_state_for_audit():
    too_senior_reviewed = make_job(
        job_key="senior-director-reviewed",
        title="Senior Director, Commercial Strategy",
        role_level="Senior Director",
        review_status="dismissed",
        dismissal_reason="role_too_senior",
        potential_priority="low",
        score_explanation="seniority_fit=too_senior; seniority_reason=likely_too_senior_senior_director",
    )

    record = row_as_record(build_review_queue_rows([too_senior_reviewed])[0])

    assert record["review_status"] == "dismissed"
    assert record["seniority_fit"] == "too_senior"


def test_review_queue_preserves_jobs_with_missing_optional_evidence():
    sparse = make_job(
        job_key="sparse",
        base_salary_min=None,
        base_salary_max=None,
        compensation_source_type="unknown",
        enrichment_match_confidence=None,
        evidence_completeness_score=5,
    )

    record = row_as_record(build_review_queue_rows([sparse])[0])

    assert record["base_salary_min"] == ""
    assert record["base_salary_max"] == ""
    assert record["compensation_source_type"] == "unknown"
    assert record["enrichment_match_confidence"] == ""


def test_review_queue_sorting_prioritizes_manual_priority_action_status_and_scores():
    lower_score = make_job(job_key="lower", manual_priority=3, review_status="not_reviewed", potential_priority_score=50)
    higher_score = make_job(job_key="higher", manual_priority=3, review_status="not_reviewed", potential_priority_score=90)
    action = make_job(job_key="action", manual_priority=3, review_status="review_now", potential_priority_score=10)
    manual_top = make_job(job_key="manual-top", manual_priority=5, review_status="watch", potential_priority_score=10)

    sorted_keys = [job.job_key for job in sort_review_queue_jobs([lower_score, higher_score, action, manual_top])]

    assert sorted_keys == ["manual-top", "action", "higher", "lower"]


def test_review_queue_values_handle_empty_workbook():
    values = build_review_queue_values([])

    assert values == [REVIEW_QUEUE_HEADERS]


def test_review_queue_generation_is_idempotent_and_does_not_update_jobs():
    job = make_job(review_status="interested", manual_priority=4, review_notes="Applied through company site.")
    sheet_client = _FakeSheetClient([job])

    first = apply_review_queue(sheet_client)
    second = apply_review_queue(sheet_client)

    assert first.review_queue_rows == 1
    assert second.review_queue_rows == 1
    assert sheet_client.review_worksheet.clear_calls == 2
    assert sheet_client.updated_jobs == []
    assert sheet_client.review_worksheet.update_calls[0][1] == sheet_client.review_worksheet.update_calls[1][1]


def test_review_queue_applies_basic_filters_and_freezes_to_review_queue_and_jobs():
    sheet_client = _FakeSheetClient([make_job()])

    result = apply_review_queue(sheet_client)

    assert result.review_queue_filter_applied is True
    assert result.review_queue_freeze_applied is True
    assert result.jobs_filter_applied is True
    assert result.jobs_freeze_applied is True
    requests = [request for call in sheet_client.workbook.batch_update_calls for request in call["requests"]]
    review_freeze = requests[0]["updateSheetProperties"]["properties"]["gridProperties"]
    jobs_freeze = next(
        request["updateSheetProperties"]["properties"]["gridProperties"]
        for request in requests
        if request.get("updateSheetProperties", {}).get("properties", {}).get("sheetId") == 1002
    )
    assert review_freeze == {"frozenRowCount": 1, "frozenColumnCount": 7}
    assert jobs_freeze == {"frozenRowCount": 1, "frozenColumnCount": 4}
    assert any("setBasicFilter" in request for request in requests)


def test_specific_priority_roles_appear_in_review_queue():
    jobs = [
        make_job(job_key="topgolf", company="Topgolf", title="Sr Manager, Strategic Planning", role_level="Senior Manager", application_status="applied", interest_decision="interested"),
        make_job(job_key="osteal", company="Osteal Therapeutics", title="Director, Commercial Operations", move_value_classification="clearly_better"),
        make_job(job_key="toyota", company="Toyota North America", title="National Manager, Product", role_level="Manager", enrichment_status="not_found", score_explanation="seniority_fit=target; seniority_reason=target_seniority_manager"),
        make_job(job_key="divcon", company="divcon", title="Director of Product Strategy", compensation_source_type="unknown"),
    ]

    records = [row_as_record(row) for row in build_review_queue_rows(jobs)]
    visible = {(record["company"], record["title"]) for record in records}

    assert ("Topgolf", "Sr Manager, Strategic Planning") in visible
    assert ("Osteal Therapeutics", "Director, Commercial Operations") in visible
    assert ("Toyota North America", "National Manager, Product") in visible
    assert ("divcon", "Director of Product Strategy") in visible


def test_dashboard_and_digest_still_render_with_review_queue_jobs():
    jobs = [make_job()]
    digest_values = build_digest_values(jobs, as_of="2026-06-29")
    dashboard_values = build_dashboard_values(jobs, digest_rows=digest_values[5:], rejected_job_rows=[])
    flattened = "\n".join(str(cell) for row in dashboard_values for cell in row)

    assert digest_values[4][0] == "digest_section"
    assert "Job Market Tracker Dashboard" in flattened
    assert "Top roles to review" in flattened
