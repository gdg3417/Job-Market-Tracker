from __future__ import annotations

from copy import deepcopy

import pytest

from src.schema import CANONICAL_SCHEMA
from src.workbook_capacity import (
    CORE_METADATA_FIELDS,
    FORMAT_METADATA_FIELDS,
    GOOGLE_SHEETS_CELL_LIMIT,
    FormattingEvidence,
    audit_sheet,
    audit_workbook,
    build_compaction_requests,
    compact_workbook,
)


def _cell(**values):
    return values


def _sheet(
    *,
    title: str = "Scratch",
    sheet_id: int = 1,
    rows: int = 100,
    columns: int = 10,
    data: list[dict] | None = None,
    **extra,
) -> dict:
    return {
        "properties": {
            "sheetId": sheet_id,
            "title": title,
            "gridProperties": {"rowCount": rows, "columnCount": columns},
        },
        "data": data or [],
        **extra,
    }


def _data_at(row: int, column: int, cell: dict) -> dict:
    return {
        "startRow": row - 1,
        "startColumn": column - 1,
        "rowData": [{"values": [cell]}],
    }


class _FakeWorkbook:
    def __init__(self, metadata: dict, format_responses: dict[str, dict] | None = None) -> None:
        self.metadata = deepcopy(metadata)
        self.format_responses = deepcopy(format_responses or {})
        self.batch_updates: list[dict] = []
        self.metadata_requests: list[dict] = []

    def fetch_sheet_metadata(self, params=None) -> dict:
        self.metadata_requests.append(deepcopy(params or {}))
        if params == {"includeGridData": True, "fields": CORE_METADATA_FIELDS}:
            return deepcopy(self.metadata)
        assert params.get("includeGridData") is True
        assert params.get("fields") == FORMAT_METADATA_FIELDS
        return deepcopy(self.format_responses.get(str(params.get("ranges") or ""), {"sheets": []}))

    def batch_update(self, body: dict) -> None:
        self.batch_updates.append(deepcopy(body))
        by_id = {
            int(sheet["properties"]["sheetId"]): sheet
            for sheet in self.metadata.get("sheets") or []
        }
        for request in body.get("requests") or []:
            dimension = request["deleteDimension"]["range"]
            sheet = by_id[int(dimension["sheetId"])]
            grid = sheet["properties"]["gridProperties"]
            removed = int(dimension["endIndex"]) - int(dimension["startIndex"])
            key = "columnCount" if dimension["dimension"] == "COLUMNS" else "rowCount"
            grid[key] -= removed


class _FakeSheetClient:
    def __init__(self, metadata: dict, format_responses: dict[str, dict] | None = None) -> None:
        self.workbook = _FakeWorkbook(metadata, format_responses)


def test_normal_sized_workbook_has_no_compaction_plan():
    sheet = _sheet(
        rows=100,
        columns=5,
        data=[_data_at(1, 5, _cell(userEnteredValue={"stringValue": "header"}))],
    )

    result = audit_sheet(sheet)

    assert result.allocated_cells == 500
    assert result.target_rows == 100
    assert result.target_columns == 5
    assert result.estimated_reclaimable_cells == 0
    assert result.safe_to_compact is False


def test_oversized_blank_jobs_grid_preserves_canonical_columns_and_requires_formatting_approval():
    result = audit_sheet(_sheet(title="Jobs", rows=1000, columns=5000))

    assert result.canonical_columns == len(CANONICAL_SCHEMA["Jobs"].headers)
    assert result.target_columns == len(CANONICAL_SCHEMA["Jobs"].headers) + 2
    assert result.target_rows == 125
    assert result.estimated_reclaimable_cells > 4_000_000
    assert result.safe_to_compact is False
    assert result.safe_to_compact_with_formatting_approval is True
    assert result.requires_blank_formatting_approval is True


def test_boolean_false_is_still_a_populated_cell():
    result = audit_sheet(
        _sheet(
            rows=1000,
            columns=1000,
            data=[_data_at(4, 250, _cell(userEnteredValue={"boolValue": False}))],
        )
    )

    assert result.populated_cells == 1
    assert result.highest_populated_column == 250
    assert result.target_columns == 252


def test_populated_cell_beyond_canonical_schema_is_never_removed_even_with_formatting_approval():
    sheet = _sheet(
        title="Jobs",
        rows=1000,
        columns=1000,
        data=[_data_at(4, 250, _cell(userEnteredValue={"stringValue": "manual evidence"}))],
    )
    client = _FakeSheetClient({"sheets": [sheet]})

    audit = audit_workbook(client)
    requests = build_compaction_requests(audit, allow_trim_blank_formatting=True)

    result = audit.sheets[0]
    assert result.highest_populated_column == 250
    assert result.target_columns == 252
    column_request = next(
        request
        for request in requests
        if request["deleteDimension"]["range"]["dimension"] == "COLUMNS"
    )
    assert column_request["deleteDimension"]["range"]["startIndex"] == 252


def test_formula_outside_normal_schema_is_preserved():
    result = audit_sheet(
        _sheet(
            title="Jobs",
            rows=1000,
            columns=1000,
            data=[
                _data_at(
                    2,
                    300,
                    _cell(
                        userEnteredValue={"formulaValue": "=A1"},
                        effectiveValue={"stringValue": "x"},
                    ),
                )
            ],
        )
    )

    assert result.formula_cells == 1
    assert result.highest_formula_column == 300
    assert result.target_columns == 302


def test_notes_validations_dimension_metadata_and_chart_anchor_are_hard_preservation_boundaries():
    data = [
        _data_at(2, 400, _cell(note="review")),
        _data_at(2, 450, _cell(dataValidation={"condition": {"type": "BOOLEAN"}})),
        {
            "startColumn": 549,
            "columnMetadata": [{"pixelSize": 180}],
        },
    ]
    result = audit_sheet(
        _sheet(
            rows=1000,
            columns=1000,
            data=data,
            charts=[
                {
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {"rowIndex": 5, "columnIndex": 599}
                        }
                    }
                }
            ],
        ),
        formatting_evidence=FormattingEvidence(
            formatted_cells={(2, 700)},
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.note_cells == 1
    assert result.validation_cells == 1
    assert result.highest_hard_metadata_column == 600
    assert result.target_columns == 602
    assert result.highest_formatting_column == 700
    assert result.column_compaction_requires_formatting_approval is True


def test_custom_formatting_outside_target_blocks_default_compaction_but_explicit_approval_allows_it():
    sheet = _sheet(rows=100, columns=20)
    evidence = FormattingEvidence(
        formatted_cells={(1, 20)},
        row_scan_complete=True,
        column_scan_complete=True,
    )
    sheet_audit = audit_sheet(sheet, formatting_evidence=evidence)
    workbook_audit = _workbook_audit_from_sheet(sheet_audit)

    assert build_compaction_requests(workbook_audit) == []
    approved = build_compaction_requests(
        workbook_audit,
        allow_trim_blank_formatting=True,
    )
    assert len(approved) == 1
    assert approved[0]["deleteDimension"]["range"]["dimension"] == "COLUMNS"


def test_complete_clean_format_scan_identifies_truly_unused_grid_and_needs_no_approval():
    result = audit_sheet(
        _sheet(rows=100, columns=20),
        formatting_evidence=FormattingEvidence(
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.estimated_reclaimable_cells == 1700
    assert result.truly_unused_grid_cells == 1700
    assert result.blank_formatted_grid_cells_detected == 0
    assert result.formatting_unverified_grid_cells == 0
    assert result.blank_formatted_or_unverified_grid_cells == 0
    assert result.reclaimable_without_formatting_approval == 1700
    assert result.safe_to_compact is True


def test_unbounded_structural_range_requires_manual_inspection_and_blocks_compaction():
    result = audit_sheet(
        _sheet(
            rows=1000,
            columns=1000,
            protectedRanges=[
                {
                    "range": {
                        "sheetId": 1,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                    }
                }
            ],
        )
    )

    assert result.unknown_ranges
    assert result.target_rows == 1000
    assert result.target_columns == 1000
    assert result.safe_to_compact is False
    assert result.safe_to_compact_with_formatting_approval is False


def test_compaction_is_explicit_and_idempotent_with_blank_formatting_approval():
    client = _FakeSheetClient({"sheets": [_sheet(rows=1000, columns=5000)]})

    preview = compact_workbook(
        client,
        apply=False,
        allow_trim_blank_formatting=True,
    )
    assert preview.applied is False
    assert preview.requests_planned == 2
    assert preview.requests_submitted == 0
    assert client.workbook.batch_updates == []

    first = compact_workbook(
        client,
        apply=True,
        allow_trim_blank_formatting=True,
    )
    assert first.applied is True
    assert first.requests_submitted == 2
    assert first.sheets_compacted == 1
    assert first.cells_reclaimed == 4_999_625
    assert first.after.allocated_cells == 375

    second = compact_workbook(
        client,
        apply=True,
        allow_trim_blank_formatting=True,
    )
    assert second.requests_submitted == 0
    assert second.cells_reclaimed == 0
    assert len(client.workbook.batch_updates) == 1


def test_compaction_without_formatting_approval_does_not_delete_unverified_blank_grid():
    client = _FakeSheetClient({"sheets": [_sheet(rows=1000, columns=5000)]})

    result = compact_workbook(client, apply=True)

    assert result.status == "blocked"
    assert result.requested_apply is True
    assert result.applied is False
    assert result.requests_planned == 0
    assert result.requests_submitted == 0
    assert client.workbook.batch_updates == []
    assert result.before.estimated_reclaimable_cells > 0
    assert result.before.reclaimable_without_formatting_approval == 0


def test_warning_and_critical_thresholds_are_reported():
    warning_client = _FakeSheetClient({"sheets": [_sheet(rows=1000, columns=8500)]})
    critical_client = _FakeSheetClient({"sheets": [_sheet(rows=1000, columns=9500)]})

    warning = audit_workbook(warning_client)
    critical = audit_workbook(critical_client)

    assert warning.allocated_cells == 8_500_000
    assert warning.capacity_ratio == pytest.approx(0.85)
    assert warning.warning is True
    assert warning.critical is False
    assert warning.classification == "warning"
    assert critical.allocated_cells < GOOGLE_SHEETS_CELL_LIMIT
    assert critical.critical is True
    assert critical.classification == "critical"


def test_invalid_threshold_order_is_rejected():
    client = _FakeSheetClient({"sheets": []})

    with pytest.raises(ValueError, match="0 < warning < critical <= 1"):
        audit_workbook(client, warning_threshold=0.95, critical_threshold=0.90)


def _workbook_audit_from_sheet(sheet_audit):
    from src.workbook_capacity import WorkbookCapacityAudit

    allocated = sheet_audit.allocated_cells
    return WorkbookCapacityAudit(
        generated_at="2026-07-13T00:00:00Z",
        sheets=[sheet_audit],
        allocated_cells=allocated,
        estimated_reclaimable_cells=sheet_audit.estimated_reclaimable_cells,
        reclaimable_without_formatting_approval=(
            sheet_audit.reclaimable_without_formatting_approval
        ),
        cell_limit=GOOGLE_SHEETS_CELL_LIMIT,
        capacity_ratio=allocated / GOOGLE_SHEETS_CELL_LIMIT,
        capacity_percent=(allocated / GOOGLE_SHEETS_CELL_LIMIT) * 100,
        warning_threshold=0.80,
        critical_threshold=0.90,
        classification="healthy",
    )
