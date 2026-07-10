from __future__ import annotations

from copy import deepcopy

from src.models import (
    JOB_FIELDS,
    VALID_APPLICATION_STATUSES,
    VALID_REVIEW_STATUSES,
)
from src.review_queue import REVIEW_QUEUE_HEADERS
from src.sheet_governance import (
    EDITABLE_HEADER_COLOR,
    GENERATED_SURFACE_POLICIES,
    JOBS_CONTROLLED_FIELDS,
    JOBS_EDITABLE_FIELDS,
    SHEET_GUIDE,
    SHEET_POLICIES,
    SYSTEM_HEADER_COLOR,
    apply_sheet_governance,
    build_sheet_requests,
    validate_governance_definitions,
)
from src.sheet_governance_policy import (
    MANUAL_FIT_RATING_OPTIONS,
    MANUAL_PRIORITY_OPTIONS,
    WORK_MODEL_SOURCE_OPTIONS,
)


def _request_types(requests):
    return [next(iter(request)) for request in requests]


def _validation_requests(requests):
    return [request["setDataValidation"] for request in requests if "setDataValidation" in request]


def _header_color_requests(requests):
    return [request["repeatCell"] for request in requests if "repeatCell" in request]


def _rgb_to_hex(rgb):
    values = [round(float(rgb[key]) * 255) for key in ("red", "green", "blue")]
    return "#" + "".join(f"{value:02X}" for value in values)


def test_governance_definitions_are_valid_and_jobs_fields_exist():
    result = validate_governance_definitions()
    assert result.ok is True
    assert result.errors == ()
    assert JOBS_EDITABLE_FIELDS <= set(JOB_FIELDS)
    assert set(JOBS_CONTROLLED_FIELDS) <= JOBS_EDITABLE_FIELDS


def test_jobs_controlled_options_reuse_existing_model_values_and_workflow_scales():
    assert set(JOBS_CONTROLLED_FIELDS["review_status"]) == VALID_REVIEW_STATUSES
    assert set(JOBS_CONTROLLED_FIELDS["application_status"]) == VALID_APPLICATION_STATUSES
    assert JOBS_CONTROLLED_FIELDS["manual_priority"] == MANUAL_PRIORITY_OPTIONS == (
        "",
        "1",
        "2",
        "3",
        "4",
        "5",
    )
    assert JOBS_CONTROLLED_FIELDS["manual_fit_rating"] == MANUAL_FIT_RATING_OPTIONS == (
        "",
        *tuple(str(value) for value in range(1, 11)),
    )
    assert JOBS_CONTROLLED_FIELDS["work_model_source"] == WORK_MODEL_SOURCE_OPTIONS
    assert WORK_MODEL_SOURCE_OPTIONS[0] == ""
    assert "user_entered" in WORK_MODEL_SOURCE_OPTIONS


def test_optional_jobs_dropdowns_include_blank_clear_value():
    for field in (
        "interest_decision",
        "manual_priority",
        "manual_fit_rating",
        "dismissal_reason",
        "application_status",
        "work_model_source",
        "required_office_days_per_week",
    ):
        assert JOBS_CONTROLLED_FIELDS[field][0] == ""


def test_jobs_requests_color_only_safe_manual_headers_green():
    headers = [
        "job_key",
        "company",
        "review_status",
        "review_notes",
        "application_status",
        "total_score",
    ]
    requests, editable, system, dropdowns, filters, freezes = build_sheet_requests(
        sheet_id=101,
        headers=headers,
        row_count=1000,
        policy=SHEET_POLICIES["Jobs"],
    )

    assert editable == 3
    assert system == 3
    assert dropdowns == 2
    assert filters == 1
    assert freezes == 1

    colors = _header_color_requests(requests)
    assert _rgb_to_hex(colors[0]["cell"]["userEnteredFormat"]["backgroundColor"]) == SYSTEM_HEADER_COLOR
    green_requests = [
        request for request in colors
        if _rgb_to_hex(request["cell"]["userEnteredFormat"]["backgroundColor"]) == EDITABLE_HEADER_COLOR
    ]
    assert len(green_requests) == 1
    assert green_requests[0]["range"]["startColumnIndex"] == 2
    assert green_requests[0]["range"]["endColumnIndex"] == 5


def test_jobs_requests_add_strict_dropdowns_to_controlled_fields():
    headers = [
        "company",
        "review_status",
        "manual_priority",
        "manual_fit_rating",
        "application_status",
        "work_model",
        "work_model_source",
        "review_notes",
    ]
    requests, *_ = build_sheet_requests(
        sheet_id=12,
        headers=headers,
        row_count=250,
        policy=SHEET_POLICIES["Jobs"],
    )
    validations = _validation_requests(requests)

    assert len(validations) == 6
    assert {item["range"]["startColumnIndex"] for item in validations} == {1, 2, 3, 4, 5, 6}
    assert all(item["rule"]["strict"] is True for item in validations)
    assert all(item["rule"]["showCustomUi"] is True for item in validations)
    assert all(item["rule"]["condition"]["type"] == "ONE_OF_LIST" for item in validations)


def test_generated_surfaces_remain_read_only_and_filterable():
    for sheet_name, policy in GENERATED_SURFACE_POLICIES.items():
        headers = ["company", "title", "review_status"]
        requests, editable, system, dropdowns, filters, freezes = build_sheet_requests(
            sheet_id=33,
            headers=headers,
            row_count=100,
            policy=policy,
        )
        assert editable == 0
        assert system == len(headers)
        assert dropdowns == 0
        assert "setDataValidation" not in _request_types(requests)
        assert freezes == 1
        assert filters == (0 if sheet_name == "Dashboard" else 1)


def test_config_tabs_are_green_and_boolean_fields_have_dropdowns():
    policy = SHEET_POLICIES["Config_Searches"]
    headers = ["search_id", "bucket", "remote_allowed", "hybrid_allowed", "active", "notes"]
    requests, editable, system, dropdowns, *_ = build_sheet_requests(
        sheet_id=44,
        headers=headers,
        row_count=1000,
        policy=policy,
    )
    assert editable == len(headers)
    assert system == 0
    assert dropdowns == 3
    assert len(_validation_requests(requests)) == 3


def test_governance_requests_never_merge_cells_or_change_schema_order():
    before = tuple(JOB_FIELDS)
    requests, *_ = build_sheet_requests(
        sheet_id=5,
        headers=list(JOB_FIELDS),
        row_count=1000,
        policy=SHEET_POLICIES["Jobs"],
    )
    after = tuple(JOB_FIELDS)

    assert before == after
    assert all("mergeCells" not in request for request in requests)
    assert all("unmergeCells" not in request for request in requests)


def test_review_queue_keeps_key_review_context_fields():
    assert {
        "job_key",
        "company",
        "title",
        "seniority_fit",
        "review_status",
        "review_notes",
        "next_action",
        "application_status",
    } <= set(REVIEW_QUEUE_HEADERS)


class WorksheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self, title, sheet_id, headers, rows=None, row_count=1000):
        self.title = title
        self.id = sheet_id
        self._headers = list(headers)
        self.rows = deepcopy(rows or [])
        self.row_count = row_count
        self.clear_calls = 0
        self.update_calls = []

    def row_values(self, row):
        assert row >= 1
        return list(self._headers)

    def clear(self):
        self.clear_calls += 1

    def update(self, **kwargs):
        self.update_calls.append(deepcopy(kwargs))


class FakeWorkbook:
    def __init__(self):
        self.batch_updates = []

    def batch_update(self, payload):
        self.batch_updates.append(deepcopy(payload))


class FakeSheetClient:
    def __init__(self, worksheets):
        self.worksheets = dict(worksheets)
        self.workbook = FakeWorkbook()
        self.next_sheet_id = 900

    def get_worksheet(self, worksheet_name):
        if worksheet_name not in self.worksheets:
            raise WorksheetNotFound(worksheet_name)
        return self.worksheets[worksheet_name]

    def ensure_worksheet(self, worksheet_name, *, rows=1000, cols=26):
        if worksheet_name not in self.worksheets:
            self.next_sheet_id += 1
            self.worksheets[worksheet_name] = FakeWorksheet(
                worksheet_name,
                self.next_sheet_id,
                [],
                row_count=rows,
            )
        return self.worksheets[worksheet_name]


def test_apply_governance_preserves_manual_data_and_only_writes_guide_values():
    jobs_rows = [["job-1", "Example Co", "interested", "Keep this note"]]
    jobs = FakeWorksheet(
        "Jobs",
        1,
        ["job_key", "company", "review_status", "review_notes"],
        rows=jobs_rows,
    )
    review_queue = FakeWorksheet(
        "Review_Queue",
        2,
        ["job_key", "company", "title", "review_status", "review_notes"],
    )
    weekly_value = FakeWorksheet(
        "Weekly_Value",
        3,
        ["Week Start", "Week End", "Jobs Added"],
    )
    client = FakeSheetClient(
        {"Jobs": jobs, "Review_Queue": review_queue, "Weekly_Value": weekly_value}
    )
    before_rows = deepcopy(jobs.rows)

    result = apply_sheet_governance(client)

    assert result.sheets_governed == 3
    assert result.guide_written is True
    assert jobs.rows == before_rows
    assert jobs.clear_calls == 0
    assert jobs.update_calls == []
    assert review_queue.clear_calls == 0
    assert review_queue.update_calls == []
    assert weekly_value.clear_calls == 0
    assert weekly_value.update_calls == []

    guide = client.worksheets[SHEET_GUIDE]
    assert guide.clear_calls == 1
    assert len(guide.update_calls) == 1
    assert "Green" in str(guide.update_calls[0]["values"])
    assert "Gray" in str(guide.update_calls[0]["values"])

    assert len(client.workbook.batch_updates) == 1
    requests = client.workbook.batch_updates[0]["requests"]
    assert all("mergeCells" not in request for request in requests)
    assert all("unmergeCells" not in request for request in requests)