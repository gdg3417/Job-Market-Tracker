from __future__ import annotations

from src.schema import CANONICAL_SCHEMA
from src.workbook_capacity import FormattingEvidence, WorkbookCapacityAudit
from src.workbook_capacity_hotfix import audit_sheet, build_compaction_requests


def _sheet(*, rows: int = 1000, columns: int = 5000, **extra) -> dict:
    return {
        "properties": {
            "sheetId": 1,
            "title": "Jobs",
            "gridProperties": {"rowCount": rows, "columnCount": columns},
        },
        "data": [],
        **extra,
    }


def _workbook_audit(sheet_audit) -> WorkbookCapacityAudit:
    allocated = sheet_audit.allocated_cells
    return WorkbookCapacityAudit(
        generated_at="2026-07-14T00:00:00Z",
        sheets=[sheet_audit],
        allocated_cells=allocated,
        estimated_reclaimable_cells=sheet_audit.estimated_reclaimable_cells,
        reclaimable_without_formatting_approval=(
            sheet_audit.reclaimable_without_formatting_approval
        ),
        cell_limit=10_000_000,
        capacity_ratio=allocated / 10_000_000,
        capacity_percent=(allocated / 10_000_000) * 100,
        warning_threshold=0.80,
        critical_threshold=0.90,
        classification="critical",
    )


def test_open_ended_conditional_format_rows_block_only_row_compaction():
    result = audit_sheet(
        _sheet(
            conditionalFormats=[
                {
                    "ranges": [
                        {
                            "sheetId": 1,
                            "startRowIndex": 1,
                            "startColumnIndex": 31,
                            "endColumnIndex": 32,
                        }
                    ]
                }
            ]
        ),
        formatting_evidence=FormattingEvidence(
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.unknown_ranges == []
    assert result.target_rows == 1000
    assert result.target_columns == len(CANONICAL_SCHEMA["Jobs"].headers) + 2
    assert any("row compaction is blocked" in warning for warning in result.warnings)

    requests = build_compaction_requests(
        _workbook_audit(result),
        allow_trim_blank_formatting=True,
    )
    assert len(requests) == 1
    assert requests[0]["deleteDimension"]["range"] == {
        "sheetId": 1,
        "dimension": "COLUMNS",
        "startIndex": len(CANONICAL_SCHEMA["Jobs"].headers) + 2,
        "endIndex": 5000,
    }


def test_open_ended_columns_block_only_column_compaction():
    result = audit_sheet(
        _sheet(
            protectedRanges=[
                {
                    "range": {
                        "sheetId": 1,
                        "startRowIndex": 0,
                        "endRowIndex": 50,
                        "startColumnIndex": 0,
                    }
                }
            ]
        ),
        formatting_evidence=FormattingEvidence(
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.unknown_ranges == []
    assert result.target_rows == 125
    assert result.target_columns == 5000
    assert any("column compaction is blocked" in warning for warning in result.warnings)

    requests = build_compaction_requests(
        _workbook_audit(result),
        allow_trim_blank_formatting=True,
    )
    assert len(requests) == 1
    assert requests[0]["deleteDimension"]["range"] == {
        "sheetId": 1,
        "dimension": "ROWS",
        "startIndex": 125,
        "endIndex": 1000,
    }


def test_range_open_in_both_dimensions_blocks_all_compaction():
    result = audit_sheet(
        _sheet(
            protectedRanges=[
                {
                    "range": {
                        "sheetId": 1,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                    }
                }
            ]
        ),
        formatting_evidence=FormattingEvidence(
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.target_rows == 1000
    assert result.target_columns == 5000
    assert result.estimated_reclaimable_cells == 0
    assert build_compaction_requests(
        _workbook_audit(result),
        allow_trim_blank_formatting=True,
    ) == []


def test_shifted_record_beyond_canonical_columns_remains_a_hard_boundary():
    shifted_column = 9171
    result = audit_sheet(
        _sheet(
            columns=9305,
            data=[
                {
                    "startRow": 567,
                    "startColumn": shifted_column - 1,
                    "rowData": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": "job-shifted"}}
                            ]
                        }
                    ],
                }
            ],
        ),
        formatting_evidence=FormattingEvidence(
            row_scan_complete=True,
            column_scan_complete=True,
        ),
    )

    assert result.highest_populated_column == shifted_column
    assert result.target_columns == shifted_column + 2
