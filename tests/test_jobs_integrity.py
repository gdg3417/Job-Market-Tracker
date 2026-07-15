from __future__ import annotations

from typing import Any

import pytest

from src.jobs_integrity import (
    JOBS_CANONICAL_COLUMN_COUNT,
    JobsIntegrityError,
    JobsWriteBoundaryError,
    audit_jobs_integrity,
    jobs_canonical_end_column,
    serialize_job_record,
    validate_canonical_write_range,
    validate_jobs_a1_range,
    validate_jobs_batch_update_requests,
    validate_jobs_headers,
)
from src.models import JOB_FIELDS


class FakeWorksheet:
    def __init__(
        self,
        *,
        headers: list[str] | None = None,
        row_count: int = 100,
        col_count: int = JOBS_CANONICAL_COLUMN_COUNT,
        values: list[list[Any]] | None = None,
        sheet_id: int = 44,
    ) -> None:
        self.headers = list(headers or JOB_FIELDS)
        self.row_count = row_count
        self.col_count = col_count
        self.values = values if values is not None else [list(self.headers)]
        self.id = sheet_id
        self.update_calls: list[dict[str, Any]] = []
        self.resize_calls: list[tuple[int, int]] = []

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        return list(self.headers)

    def get_values(self, range_name: str | None = None) -> list[list[Any]]:
        return [list(row) for row in self.values]

    def update(self, **kwargs: Any) -> None:
        self.update_calls.append(kwargs)

    def resize(self, *, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))
        self.row_count = rows
        self.col_count = cols


class FakeWorkbook:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata or {"sheets": []}
        self.fetch_calls: list[dict[str, Any]] = []

    def fetch_sheet_metadata(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.fetch_calls.append(dict(params or {}))
        return self.metadata


class FakeSheetClient:
    def __init__(self, worksheet: FakeWorksheet, metadata: dict[str, Any] | None = None) -> None:
        self.worksheet = worksheet
        self.workbook = FakeWorkbook(metadata)

    def get_worksheet(self, worksheet_name: str) -> FakeWorksheet:
        assert worksheet_name == "Jobs"
        return self.worksheet


def _jobs_record() -> dict[str, Any]:
    return {field_name: "" for field_name in JOB_FIELDS}


def _metadata_with_cell(*, row: int, column: int, cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "sheets": [
            {
                "properties": {
                    "sheetId": 44,
                    "title": "Jobs",
                    "gridProperties": {"rowCount": 700, "columnCount": max(column, JOBS_CANONICAL_COLUMN_COUNT)},
                },
                "data": [
                    {
                        "startRow": row - 1,
                        "startColumn": column - 1,
                        "rowData": [{"values": [cell]}],
                    }
                ],
            }
        ]
    }


def test_canonical_constants_match_jobs_schema() -> None:
    assert JOBS_CANONICAL_COLUMN_COUNT == len(JOB_FIELDS) == 135
    assert jobs_canonical_end_column() == "EE"
    assert JOB_FIELDS[-1] == "decision_evidence_conflict_notes"


def test_jobs_headers_must_be_exact_and_ordered() -> None:
    assert validate_jobs_headers(JOB_FIELDS) == list(JOB_FIELDS)
    with pytest.raises(JobsIntegrityError, match="header count"):
        validate_jobs_headers([*JOB_FIELDS, "legacy"])
    swapped = list(JOB_FIELDS)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(JobsIntegrityError, match="order"):
        validate_jobs_headers(swapped)


def test_job_record_serializes_to_exact_canonical_width() -> None:
    record = _jobs_record()
    record["job_key"] = "acme-strategy-manager"
    row = serialize_job_record(record)
    assert len(row) == 135
    assert row[JOB_FIELDS.index("job_key")] == "acme-strategy-manager"


def test_job_record_rejects_unknown_or_missing_fields() -> None:
    extra = _jobs_record()
    extra["unexpected"] = "x"
    with pytest.raises(JobsWriteBoundaryError, match="unknown fields"):
        serialize_job_record(extra)
    missing = _jobs_record()
    missing.pop("title")
    with pytest.raises(JobsWriteBoundaryError, match="missing canonical fields"):
        serialize_job_record(missing)


def test_canonical_range_ending_at_ee_passes_and_ef_fails() -> None:
    validate_canonical_write_range("Jobs", 2, 1, 1, 135, operation_name="test")
    with pytest.raises(JobsWriteBoundaryError, match="end_column=136"):
        validate_canonical_write_range("Jobs", 2, 1, 1, 136, operation_name="test")
    validate_jobs_a1_range("Jobs!A2:EE2", operation_name="test")
    with pytest.raises(JobsWriteBoundaryError, match="EF"):
        validate_jobs_a1_range("Jobs!A2:EF2", operation_name="test")


def test_zero_based_api_column_index_135_fails() -> None:
    request = {
        "updateCells": {
            "range": {"sheetId": 44, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 135, "endColumnIndex": 136},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": "x"}}]}],
            "fields": "userEnteredValue",
        }
    }
    with pytest.raises(JobsWriteBoundaryError, match="start_column=136"):
        validate_jobs_batch_update_requests([request], jobs_sheet_id=44, operation_name="test updateCells")


def test_zero_based_api_range_ending_at_135_passes() -> None:
    request = {
        "updateCells": {
            "range": {"sheetId": 44, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 134, "endColumnIndex": 135},
            "rows": [{"values": [{"userEnteredValue": {"stringValue": "x"}}]}],
            "fields": "userEnteredValue",
        }
    }
    validate_jobs_batch_update_requests([request], jobs_sheet_id=44, operation_name="test updateCells")


def test_append_cells_is_rejected_for_jobs() -> None:
    with pytest.raises(JobsWriteBoundaryError, match="appendCells"):
        validate_jobs_batch_update_requests(
            [{"appendCells": {"sheetId": 44, "rows": [], "fields": "userEnteredValue"}}],
            jobs_sheet_id=44,
            operation_name="test appendCells",
        )


def test_row_dimension_formatting_does_not_trigger_column_boundary_guard() -> None:
    validate_jobs_batch_update_requests(
        [
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": 44, "dimension": "ROWS", "startIndex": 0, "endIndex": 10},
                    "properties": {"pixelSize": 21},
                    "fields": "pixelSize",
                }
            }
        ],
        jobs_sheet_id=44,
        operation_name="row formatting",
    )


def test_valid_repaired_workbook_is_healthy() -> None:
    worksheet = FakeWorksheet()
    audit = audit_jobs_integrity(FakeSheetClient(worksheet))
    assert audit.health_status == "healthy"
    assert audit.writes_allowed is True
    assert audit.grid_columns == 135
    assert audit.out_of_bounds_value_count == 0


def test_historical_lto680_fixture_is_detected_without_mutation() -> None:
    worksheet = FakeWorksheet(row_count=700, col_count=8647)
    metadata = _metadata_with_cell(
        row=680,
        column=8647,
        cell={"userEnteredValue": {"stringValue": "insufficient_evidence"}},
    )
    audit = audit_jobs_integrity(FakeSheetClient(worksheet, metadata))
    assert audit.health_status == "unsafe"
    assert audit.writes_allowed is False
    assert audit.out_of_bounds_value_count == 1
    assert audit.offending_coordinates[0].coordinate == "LTO680"
    assert audit.offending_coordinates[0].observed_value_category == "recognized controlled value"
    assert audit.offending_coordinates[0].possible_canonical_field == "move_value_classification"
    assert audit.offending_coordinates[0].canonical_row_identity_present is False
    assert worksheet.update_calls == []
    assert worksheet.resize_calls == []


def test_formula_note_validation_and_hyperlink_are_reported_distinctly() -> None:
    worksheet = FakeWorksheet(row_count=10, col_count=136)
    metadata = _metadata_with_cell(
        row=4,
        column=136,
        cell={
            "userEnteredValue": {"formulaValue": "=1+1"},
            "note": "review",
            "dataValidation": {"condition": {"type": "ONE_OF_LIST"}},
            "hyperlink": "https://example.com",
        },
    )
    audit = audit_jobs_integrity(FakeSheetClient(worksheet, metadata))
    assert audit.out_of_bounds_formula_count == 1
    assert audit.out_of_bounds_value_count == 0
    assert audit.out_of_bounds_metadata_count == 1
    assert audit.offending_coordinates[0].coordinate == "EF4"


def test_large_blank_trailing_grid_is_unsafe_but_not_reported_as_populated() -> None:
    worksheet = FakeWorksheet(row_count=1000, col_count=5000)
    metadata = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 44,
                    "title": "Jobs",
                    "gridProperties": {"rowCount": 1000, "columnCount": 5000},
                },
                "data": [],
            }
        ]
    }
    audit = audit_jobs_integrity(FakeSheetClient(worksheet, metadata))
    assert audit.health_status == "unsafe"
    assert audit.grid_columns == 5000
    assert audit.out_of_bounds_value_count == 0
    assert audit.out_of_bounds_formula_count == 0
    assert audit.out_of_bounds_metadata_count == 0


def test_structural_range_after_ee_is_reported() -> None:
    worksheet = FakeWorksheet(row_count=100, col_count=200)
    metadata = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 44,
                    "title": "Jobs",
                    "gridProperties": {"rowCount": 100, "columnCount": 200},
                },
                "basicFilter": {
                    "range": {"sheetId": 44, "startRowIndex": 0, "endRowIndex": 100, "startColumnIndex": 0, "endColumnIndex": 200}
                },
                "data": [],
            }
        ]
    }
    audit = audit_jobs_integrity(FakeSheetClient(worksheet, metadata))
    assert audit.out_of_bounds_structural_metadata_count == 1
    assert audit.offending_coordinates[0].coordinate == "basicFilter"
