from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from src.models import utc_now_iso
from src.schema import CANONICAL_SCHEMA
from src.settings import load_settings
from src.sheet_governance_policy import GENERATED_SURFACE_NAMES, SHEET_POLICIES
from src.sheets import SheetClient, with_quota_backoff

GOOGLE_SHEETS_CELL_LIMIT = 10_000_000
DEFAULT_WARNING_THRESHOLD = 0.80
DEFAULT_CRITICAL_THRESHOLD = 0.90
DEFAULT_MIN_ROWS = 100
DEFAULT_ROW_HEADROOM = 25
DEFAULT_COLUMN_HEADROOM = 2
FULL_FORMAT_SCAN_CELL_LIMIT = 100_000
FORMAT_SAMPLE_ROWS = 5
FORMAT_SAMPLE_COLUMNS = 10

CORE_METADATA_FIELDS = (
    "namedRanges,"
    "sheets("
    "properties(sheetId,title,gridProperties),"
    "merges,basicFilter,filterViews,bandedRanges,protectedRanges,conditionalFormats,"
    "charts(position),slicers(position),"
    "data("
    "startRow,startColumn,rowMetadata,columnMetadata,"
    "rowData(values("
    "userEnteredValue,effectiveValue,formattedValue,note,dataValidation,hyperlink,"
    "textFormatRuns,chipRuns"
    "))"
    ")"
    ")"
)
FORMAT_METADATA_FIELDS = (
    "sheets("
    "properties(sheetId,title),"
    "data(startRow,startColumn,rowData(values(userEnteredFormat)))"
    ")"
)


@dataclass(slots=True)
class FormattingEvidence:
    formatted_cells: set[tuple[int, int]] = field(default_factory=set)
    row_scan_complete: bool = False
    column_scan_complete: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def highest_row(self) -> int:
        return max((row for row, _ in self.formatted_cells), default=0)

    @property
    def highest_column(self) -> int:
        return max((column for _, column in self.formatted_cells), default=0)


@dataclass(slots=True)
class SheetCapacityAudit:
    worksheet_name: str
    sheet_id: int
    sheet_role: str
    allocated_rows: int
    allocated_columns: int
    allocated_cells: int
    canonical_columns: int
    highest_populated_row: int
    highest_populated_column: int
    highest_formula_row: int
    highest_formula_column: int
    highest_hard_metadata_row: int
    highest_hard_metadata_column: int
    highest_formatting_row: int
    highest_formatting_column: int
    highest_metadata_row: int
    highest_metadata_column: int
    populated_cells: int
    formula_cells: int
    note_cells: int
    validation_cells: int
    formatted_blank_cells_detected: int
    blank_formatted_grid_cells_detected: int
    formatting_unverified_grid_cells: int
    formatting_scan_complete: bool
    structural_ranges: int
    target_rows: int
    target_columns: int
    estimated_reclaimable_cells: int
    reclaimable_without_formatting_approval: int
    truly_unused_grid_cells: int
    blank_formatted_or_unverified_grid_cells: int
    row_compaction_requires_formatting_approval: bool
    column_compaction_requires_formatting_approval: bool
    unknown_ranges: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def requires_blank_formatting_approval(self) -> bool:
        return (
            self.row_compaction_requires_formatting_approval
            or self.column_compaction_requires_formatting_approval
        )

    @property
    def safe_to_compact(self) -> bool:
        return not self.unknown_ranges and self.reclaimable_without_formatting_approval > 0

    @property
    def safe_to_compact_with_formatting_approval(self) -> bool:
        return not self.unknown_ranges and self.estimated_reclaimable_cells > 0

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values.update(
            {
                "requires_blank_formatting_approval": self.requires_blank_formatting_approval,
                "safe_to_compact": self.safe_to_compact,
                "safe_to_compact_with_formatting_approval": (
                    self.safe_to_compact_with_formatting_approval
                ),
            }
        )
        return values


@dataclass(slots=True)
class WorkbookCapacityAudit:
    generated_at: str
    sheets: list[SheetCapacityAudit]
    allocated_cells: int
    estimated_reclaimable_cells: int
    reclaimable_without_formatting_approval: int
    cell_limit: int
    capacity_ratio: float
    capacity_percent: float
    warning_threshold: float
    critical_threshold: float
    classification: str
    warnings: list[str] = field(default_factory=list)

    @property
    def warning(self) -> bool:
        return self.capacity_ratio >= self.warning_threshold

    @property
    def critical(self) -> bool:
        return self.capacity_ratio >= self.critical_threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "allocated_cells": self.allocated_cells,
            "estimated_reclaimable_cells": self.estimated_reclaimable_cells,
            "reclaimable_without_formatting_approval": (
                self.reclaimable_without_formatting_approval
            ),
            "cell_limit": self.cell_limit,
            "capacity_ratio": round(self.capacity_ratio, 6),
            "capacity_percent": round(self.capacity_percent, 2),
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
            "classification": self.classification,
            "warning": self.warning,
            "critical": self.critical,
            "warnings": list(self.warnings),
            "sheets": [sheet.to_dict() for sheet in self.sheets],
        }


@dataclass(slots=True)
class CompactionResult:
    status: str
    requested_apply: bool
    applied: bool
    allow_trim_blank_formatting: bool
    requests_planned: int
    requests_submitted: int
    sheets_planned: int
    sheets_compacted: int
    cells_reclaimed: int
    before: WorkbookCapacityAudit
    after: WorkbookCapacityAudit

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_mode": (
                "workbook_capacity_compaction"
                if self.requested_apply
                else "workbook_capacity_plan"
            ),
            "status": self.status,
            "requested_apply": self.requested_apply,
            "applied": self.applied,
            "allow_trim_blank_formatting": self.allow_trim_blank_formatting,
            "requests_planned": self.requests_planned,
            "requests_submitted": self.requests_submitted,
            "sheets_planned": self.sheets_planned,
            "sheets_compacted": self.sheets_compacted,
            "cells_reclaimed": self.cells_reclaimed,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


def _sheet_role(title: str) -> str:
    if title == "Jobs":
        return "canonical_data"
    if title in GENERATED_SURFACE_NAMES:
        return "generated_data"
    policy = SHEET_POLICIES.get(title)
    if policy and (policy.all_headers_editable or policy.editable_fields or policy.dropdowns()):
        return "configuration"
    if title in CANONICAL_SCHEMA:
        return "system_ledger"
    return "unknown"


def _nonempty(value: Any) -> bool:
    if value is None or value == "" or value is False:
        return False
    if isinstance(value, dict):
        return any(_nonempty(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_nonempty(item) for item in value)
    return True


def _dimension_metadata_meaningful(metadata: dict[str, Any], *, dimension: str) -> bool:
    if not _nonempty(metadata):
        return False
    remaining = dict(metadata)
    pixel_size = remaining.pop("pixelSize", None)
    if _nonempty(remaining):
        return True
    if pixel_size is None:
        return False
    default_sizes = {"ROWS": {20, 21}, "COLUMNS": {100}}
    return int(pixel_size) not in default_sizes.get(dimension, set())


def _cell_signals(cell: dict[str, Any]) -> set[str]:
    signals: set[str] = set()
    entered = cell.get("userEnteredValue")
    effective = cell.get("effectiveValue")
    has_entered = isinstance(entered, dict) and bool(entered)
    has_effective = isinstance(effective, dict) and bool(effective)
    has_formatted = "formattedValue" in cell and cell.get("formattedValue") not in (None, "")
    if has_entered or has_effective or has_formatted:
        signals.add("value")
    if isinstance(entered, dict) and "formulaValue" in entered:
        signals.update({"formula", "value"})
    if _nonempty(cell.get("note")):
        signals.add("note")
    if _nonempty(cell.get("dataValidation")):
        signals.add("validation")
    if _nonempty(cell.get("hyperlink")) or _nonempty(cell.get("textFormatRuns")) or _nonempty(cell.get("chipRuns")):
        signals.add("metadata")
    return signals


def _range_end(
    grid_range: dict[str, Any],
    *,
    row_count: int,
    column_count: int,
    label: str,
) -> tuple[int, int, list[str]]:
    unknown: list[str] = []
    if "endRowIndex" in grid_range:
        row_end = int(grid_range.get("endRowIndex") or 0)
    else:
        row_end = row_count
        unknown.append(f"{label} has no endRowIndex")
    if "endColumnIndex" in grid_range:
        column_end = int(grid_range.get("endColumnIndex") or 0)
    else:
        column_end = column_count
        unknown.append(f"{label} has no endColumnIndex")
    return row_end, column_end, unknown


def _iter_structural_ranges(
    sheet: dict[str, Any],
    named_ranges: Iterable[dict[str, Any]],
) -> Iterable[tuple[str, dict[str, Any]]]:
    sheet_id = int((sheet.get("properties") or {}).get("sheetId") or 0)
    for index, value in enumerate(sheet.get("merges") or []):
        yield f"merge[{index}]", value
    basic_filter = sheet.get("basicFilter") or {}
    if basic_filter.get("range"):
        yield "basicFilter", basic_filter["range"]
    for key in ("filterViews", "bandedRanges", "protectedRanges"):
        for index, value in enumerate(sheet.get(key) or []):
            if value.get("range"):
                yield f"{key}[{index}]", value["range"]
            elif key == "protectedRanges":
                yield f"{key}[{index}]", {}
    for index, value in enumerate(sheet.get("conditionalFormats") or []):
        for range_index, grid_range in enumerate(value.get("ranges") or []):
            yield f"conditionalFormats[{index}].ranges[{range_index}]", grid_range
    for index, named_range in enumerate(named_ranges):
        grid_range = named_range.get("range") or {}
        if int(grid_range.get("sheetId") or 0) == sheet_id:
            yield f"namedRange[{index}]", grid_range


def _minimum_dimensions(
    title: str,
    properties: dict[str, Any],
    highest_populated_row: int,
    highest_populated_column: int,
) -> tuple[int, int]:
    policy = SHEET_POLICIES.get(title)
    header_row = int(getattr(policy, "header_row", 1) or 1)
    frozen_rows = int((properties.get("gridProperties") or {}).get("frozenRowCount") or 0)
    frozen_columns = int((properties.get("gridProperties") or {}).get("frozenColumnCount") or 0)
    canonical_columns = len(CANONICAL_SCHEMA[title].headers) if title in CANONICAL_SCHEMA else 0
    minimum_rows = max(DEFAULT_MIN_ROWS, header_row + 10, frozen_rows + 1, highest_populated_row)
    minimum_columns = max(1, canonical_columns, frozen_columns + 1, highest_populated_column)
    return minimum_rows, minimum_columns


def _formatting_outside(
    evidence: FormattingEvidence,
    *,
    target_rows: int,
    target_columns: int,
) -> tuple[bool, bool]:
    row_formatting = any(row > target_rows for row, _ in evidence.formatted_cells)
    column_formatting = any(column > target_columns for _, column in evidence.formatted_cells)
    return row_formatting, column_formatting


def audit_sheet(
    sheet: dict[str, Any],
    *,
    named_ranges: Iterable[dict[str, Any]] = (),
    formatting_evidence: FormattingEvidence | None = None,
) -> SheetCapacityAudit:
    formatting_evidence = formatting_evidence or FormattingEvidence()
    properties = sheet.get("properties") or {}
    grid = properties.get("gridProperties") or {}
    title = str(properties.get("title") or "")
    sheet_id = int(properties.get("sheetId") or 0)
    row_count = max(1, int(grid.get("rowCount") or 1))
    column_count = max(1, int(grid.get("columnCount") or 1))

    highest_populated_row = highest_populated_column = 0
    highest_formula_row = highest_formula_column = 0
    highest_hard_metadata_row = highest_hard_metadata_column = 0
    populated_cells = formula_cells = note_cells = validation_cells = 0

    for grid_data in sheet.get("data") or []:
        start_row = int(grid_data.get("startRow") or 0)
        start_column = int(grid_data.get("startColumn") or 0)
        for row_offset, metadata in enumerate(grid_data.get("rowMetadata") or []):
            if _dimension_metadata_meaningful(metadata, dimension="ROWS"):
                highest_hard_metadata_row = max(
                    highest_hard_metadata_row,
                    start_row + row_offset + 1,
                )
        for column_offset, metadata in enumerate(grid_data.get("columnMetadata") or []):
            if _dimension_metadata_meaningful(metadata, dimension="COLUMNS"):
                highest_hard_metadata_column = max(
                    highest_hard_metadata_column,
                    start_column + column_offset + 1,
                )
        for row_offset, row_data in enumerate(grid_data.get("rowData") or []):
            for column_offset, cell in enumerate(row_data.get("values") or []):
                signals = _cell_signals(cell)
                if not signals:
                    continue
                row_number = start_row + row_offset + 1
                column_number = start_column + column_offset + 1
                if "value" in signals:
                    populated_cells += 1
                    highest_populated_row = max(highest_populated_row, row_number)
                    highest_populated_column = max(highest_populated_column, column_number)
                if "formula" in signals:
                    formula_cells += 1
                    highest_formula_row = max(highest_formula_row, row_number)
                    highest_formula_column = max(highest_formula_column, column_number)
                if "note" in signals:
                    note_cells += 1
                if "validation" in signals:
                    validation_cells += 1
                if signals - {"value", "formula"}:
                    highest_hard_metadata_row = max(highest_hard_metadata_row, row_number)
                    highest_hard_metadata_column = max(
                        highest_hard_metadata_column,
                        column_number,
                    )

    for object_type in ("charts", "slicers"):
        for value in sheet.get(object_type) or []:
            anchor = (
                ((value.get("position") or {}).get("overlayPosition") or {}).get("anchorCell")
                or {}
            )
            if anchor:
                highest_hard_metadata_row = max(
                    highest_hard_metadata_row,
                    int(anchor.get("rowIndex") or 0) + 1,
                )
                highest_hard_metadata_column = max(
                    highest_hard_metadata_column,
                    int(anchor.get("columnIndex") or 0) + 1,
                )

    structural_ranges = 0
    unknown_ranges: list[str] = []
    highest_structural_row = highest_structural_column = 0
    for label, grid_range in _iter_structural_ranges(sheet, named_ranges):
        structural_ranges += 1
        row_end, column_end, unknown = _range_end(
            grid_range,
            row_count=row_count,
            column_count=column_count,
            label=label,
        )
        highest_structural_row = max(highest_structural_row, row_end)
        highest_structural_column = max(highest_structural_column, column_end)
        unknown_ranges.extend(unknown)

    highest_hard_metadata_row = max(
        highest_hard_metadata_row,
        highest_structural_row,
    )
    highest_hard_metadata_column = max(
        highest_hard_metadata_column,
        highest_structural_column,
    )
    meaningful_row = max(
        highest_populated_row,
        highest_formula_row,
        highest_hard_metadata_row,
    )
    meaningful_column = max(
        highest_populated_column,
        highest_formula_column,
        highest_hard_metadata_column,
    )
    minimum_rows, minimum_columns = _minimum_dimensions(
        title,
        properties,
        highest_populated_row,
        highest_populated_column,
    )
    target_rows = min(
        row_count,
        max(minimum_rows, meaningful_row) + DEFAULT_ROW_HEADROOM,
    )
    target_columns = min(
        column_count,
        max(minimum_columns, meaningful_column) + DEFAULT_COLUMN_HEADROOM,
    )
    if unknown_ranges:
        target_rows = row_count
        target_columns = column_count

    row_formatting, column_formatting = _formatting_outside(
        formatting_evidence,
        target_rows=target_rows,
        target_columns=target_columns,
    )
    row_delete_possible = target_rows < row_count
    column_delete_possible = target_columns < column_count
    row_approval = row_delete_possible and (
        not formatting_evidence.row_scan_complete or row_formatting
    )
    column_approval = column_delete_possible and (
        not formatting_evidence.column_scan_complete or column_formatting
    )

    allocated_cells = row_count * column_count
    approved_target_cells = target_rows * target_columns
    reclaimable = max(0, allocated_cells - approved_target_cells)

    no_approval_rows = row_count if row_approval else target_rows
    no_approval_columns = column_count if column_approval else target_columns
    reclaimable_without_approval = max(
        0,
        allocated_cells - (no_approval_rows * no_approval_columns),
    )

    formatted_cells_in_delete_region = {
        (row, column)
        for row, column in formatting_evidence.formatted_cells
        if row > target_rows or column > target_columns
    }
    candidate_formatting_scan_complete = (
        (not row_delete_possible or formatting_evidence.row_scan_complete)
        and (not column_delete_possible or formatting_evidence.column_scan_complete)
    )
    formatted_blank_detected = len(formatted_cells_in_delete_region)
    if candidate_formatting_scan_complete:
        truly_unused = max(0, reclaimable - formatted_blank_detected)
        formatting_unverified = 0
    else:
        truly_unused = 0
        formatting_unverified = max(0, reclaimable - formatted_blank_detected)

    highest_formatting_row = formatting_evidence.highest_row
    highest_formatting_column = formatting_evidence.highest_column
    warnings = list(formatting_evidence.warnings)
    if row_approval or column_approval:
        dimensions = []
        if row_approval:
            dimensions.append("rows")
        if column_approval:
            dimensions.append("columns")
        warnings.append(
            "Trailing " + " and ".join(dimensions) + " require explicit blank-formatting approval"
        )

    return SheetCapacityAudit(
        worksheet_name=title,
        sheet_id=sheet_id,
        sheet_role=_sheet_role(title),
        allocated_rows=row_count,
        allocated_columns=column_count,
        allocated_cells=allocated_cells,
        canonical_columns=len(CANONICAL_SCHEMA[title].headers) if title in CANONICAL_SCHEMA else 0,
        highest_populated_row=highest_populated_row,
        highest_populated_column=highest_populated_column,
        highest_formula_row=highest_formula_row,
        highest_formula_column=highest_formula_column,
        highest_hard_metadata_row=highest_hard_metadata_row,
        highest_hard_metadata_column=highest_hard_metadata_column,
        highest_formatting_row=highest_formatting_row,
        highest_formatting_column=highest_formatting_column,
        highest_metadata_row=max(highest_hard_metadata_row, highest_formatting_row),
        highest_metadata_column=max(highest_hard_metadata_column, highest_formatting_column),
        populated_cells=populated_cells,
        formula_cells=formula_cells,
        note_cells=note_cells,
        validation_cells=validation_cells,
        formatted_blank_cells_detected=len(formatting_evidence.formatted_cells),
        blank_formatted_grid_cells_detected=formatted_blank_detected,
        formatting_unverified_grid_cells=formatting_unverified,
        formatting_scan_complete=candidate_formatting_scan_complete,
        structural_ranges=structural_ranges,
        target_rows=target_rows,
        target_columns=target_columns,
        estimated_reclaimable_cells=reclaimable,
        reclaimable_without_formatting_approval=reclaimable_without_approval,
        truly_unused_grid_cells=truly_unused,
        blank_formatted_or_unverified_grid_cells=(
            formatted_blank_detected + formatting_unverified
        ),
        row_compaction_requires_formatting_approval=row_approval,
        column_compaction_requires_formatting_approval=column_approval,
        unknown_ranges=unknown_ranges,
        warnings=warnings,
    )


def _classification(
    capacity_ratio: float,
    warning_threshold: float,
    critical_threshold: float,
) -> str:
    if capacity_ratio >= critical_threshold:
        return "critical"
    if capacity_ratio >= warning_threshold:
        return "warning"
    return "healthy"


def _column_name(column_number: int) -> str:
    if column_number < 1:
        raise ValueError("Column number must be positive")
    letters = ""
    value = column_number
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _quoted_sheet_title(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _formatted_cells_from_metadata(metadata: dict[str, Any]) -> set[tuple[int, int]]:
    coordinates: set[tuple[int, int]] = set()
    for sheet in metadata.get("sheets") or []:
        for grid_data in sheet.get("data") or []:
            start_row = int(grid_data.get("startRow") or 0)
            start_column = int(grid_data.get("startColumn") or 0)
            for row_offset, row_data in enumerate(grid_data.get("rowData") or []):
                for column_offset, cell in enumerate(row_data.get("values") or []):
                    if _nonempty(cell.get("userEnteredFormat")):
                        coordinates.add(
                            (
                                start_row + row_offset + 1,
                                start_column + column_offset + 1,
                            )
                        )
    return coordinates


def _fetch_format_range(
    sheet_client: SheetClient,
    *,
    worksheet_name: str,
    a1_range: str,
) -> set[tuple[int, int]]:
    metadata = with_quota_backoff(
        lambda: sheet_client.workbook.fetch_sheet_metadata(
            params={
                "includeGridData": True,
                "ranges": f"{_quoted_sheet_title(worksheet_name)}!{a1_range}",
                "fields": FORMAT_METADATA_FIELDS,
            }
        ),
        operation_name=f"inspect workbook formatting {worksheet_name} {a1_range}",
    )
    return _formatted_cells_from_metadata(metadata)


def _inspect_trailing_formatting(
    sheet_client: SheetClient,
    sheet_audit: SheetCapacityAudit,
) -> FormattingEvidence:
    evidence = FormattingEvidence()
    if sheet_audit.unknown_ranges:
        evidence.warnings.append("Formatting inspection skipped because structural ranges are unbounded")
        return evidence

    try:
        if sheet_audit.target_columns < sheet_audit.allocated_columns:
            start_column = sheet_audit.target_columns + 1
            end_column = sheet_audit.allocated_columns
            tail_cells = sheet_audit.allocated_rows * (end_column - start_column + 1)
            scan_rows = (
                sheet_audit.allocated_rows
                if tail_cells <= FULL_FORMAT_SCAN_CELL_LIMIT
                else min(FORMAT_SAMPLE_ROWS, sheet_audit.allocated_rows)
            )
            a1_range = (
                f"{_column_name(start_column)}1:"
                f"{_column_name(end_column)}{scan_rows}"
            )
            evidence.formatted_cells.update(
                _fetch_format_range(
                    sheet_client,
                    worksheet_name=sheet_audit.worksheet_name,
                    a1_range=a1_range,
                )
            )
            evidence.column_scan_complete = scan_rows == sheet_audit.allocated_rows
            if not evidence.column_scan_complete:
                evidence.warnings.append(
                    "Trailing column formatting was sampled because the full range exceeds the scan limit"
                )
        else:
            evidence.column_scan_complete = True

        if sheet_audit.target_rows < sheet_audit.allocated_rows:
            start_row = sheet_audit.target_rows + 1
            end_row = sheet_audit.allocated_rows
            tail_cells = sheet_audit.allocated_columns * (end_row - start_row + 1)
            scan_columns = (
                sheet_audit.allocated_columns
                if tail_cells <= FULL_FORMAT_SCAN_CELL_LIMIT
                else min(FORMAT_SAMPLE_COLUMNS, sheet_audit.allocated_columns)
            )
            a1_range = (
                f"A{start_row}:"
                f"{_column_name(scan_columns)}{end_row}"
            )
            evidence.formatted_cells.update(
                _fetch_format_range(
                    sheet_client,
                    worksheet_name=sheet_audit.worksheet_name,
                    a1_range=a1_range,
                )
            )
            evidence.row_scan_complete = scan_columns == sheet_audit.allocated_columns
            if not evidence.row_scan_complete:
                evidence.warnings.append(
                    "Trailing row formatting was sampled because the full range exceeds the scan limit"
                )
        else:
            evidence.row_scan_complete = True
    except Exception as exc:
        evidence.warnings.append(
            f"Formatting inspection failed: {exc.__class__.__name__}: {exc}"
        )
    return evidence


def _fetch_core_metadata(sheet_client: SheetClient) -> dict[str, Any]:
    return with_quota_backoff(
        lambda: sheet_client.workbook.fetch_sheet_metadata(
            params={
                "includeGridData": True,
                "fields": CORE_METADATA_FIELDS,
            }
        ),
        operation_name="fetch workbook capacity metadata",
    )


def audit_workbook(
    sheet_client: SheetClient,
    *,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
) -> WorkbookCapacityAudit:
    if not 0 < warning_threshold < critical_threshold <= 1:
        raise ValueError("Capacity thresholds must satisfy 0 < warning < critical <= 1")

    metadata = _fetch_core_metadata(sheet_client)
    named_ranges = metadata.get("namedRanges") or []
    preliminary = [
        audit_sheet(sheet, named_ranges=named_ranges)
        for sheet in metadata.get("sheets") or []
    ]
    formatting_by_sheet = {
        sheet.sheet_id: _inspect_trailing_formatting(sheet_client, sheet)
        for sheet in preliminary
        if sheet.estimated_reclaimable_cells > 0 and not sheet.unknown_ranges
    }
    sheet_audits = [
        audit_sheet(
            sheet,
            named_ranges=named_ranges,
            formatting_evidence=formatting_by_sheet.get(
                int((sheet.get("properties") or {}).get("sheetId") or 0),
                FormattingEvidence(row_scan_complete=True, column_scan_complete=True),
            ),
        )
        for sheet in metadata.get("sheets") or []
    ]

    allocated_cells = sum(sheet.allocated_cells for sheet in sheet_audits)
    reclaimable = sum(sheet.estimated_reclaimable_cells for sheet in sheet_audits)
    reclaimable_without_approval = sum(
        sheet.reclaimable_without_formatting_approval for sheet in sheet_audits
    )
    ratio = allocated_cells / GOOGLE_SHEETS_CELL_LIMIT
    warnings: list[str] = []
    for sheet in sheet_audits:
        warnings.extend(
            f"{sheet.worksheet_name}: {warning}"
            for warning in [*sheet.unknown_ranges, *sheet.warnings]
        )
    return WorkbookCapacityAudit(
        generated_at=utc_now_iso(),
        sheets=sheet_audits,
        allocated_cells=allocated_cells,
        estimated_reclaimable_cells=reclaimable,
        reclaimable_without_formatting_approval=reclaimable_without_approval,
        cell_limit=GOOGLE_SHEETS_CELL_LIMIT,
        capacity_ratio=ratio,
        capacity_percent=ratio * 100,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        classification=_classification(ratio, warning_threshold, critical_threshold),
        warnings=warnings,
    )


def build_compaction_requests(
    audit: WorkbookCapacityAudit,
    *,
    allow_trim_blank_formatting: bool = False,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for sheet in audit.sheets:
        if sheet.unknown_ranges:
            continue
        if sheet.target_columns < sheet.allocated_columns and (
            allow_trim_blank_formatting
            or not sheet.column_compaction_requires_formatting_approval
        ):
            requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet.sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": sheet.target_columns,
                            "endIndex": sheet.allocated_columns,
                        }
                    }
                }
            )
        if sheet.target_rows < sheet.allocated_rows and (
            allow_trim_blank_formatting
            or not sheet.row_compaction_requires_formatting_approval
        ):
            requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet.sheet_id,
                            "dimension": "ROWS",
                            "startIndex": sheet.target_rows,
                            "endIndex": sheet.allocated_rows,
                        }
                    }
                }
            )
    return requests


def compact_workbook(
    sheet_client: SheetClient,
    *,
    apply: bool,
    allow_trim_blank_formatting: bool = False,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
) -> CompactionResult:
    before = audit_workbook(
        sheet_client,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
    )
    requests = build_compaction_requests(
        before,
        allow_trim_blank_formatting=allow_trim_blank_formatting,
    )
    planned_sheet_ids = {
        int(request["deleteDimension"]["range"]["sheetId"])
        for request in requests
    }
    if apply and requests:
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name="apply workbook capacity compaction",
        )
        after = audit_workbook(
            sheet_client,
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
        )
    else:
        after = before
    cells_reclaimed = max(0, before.allocated_cells - after.allocated_cells) if apply else 0
    changes_applied = apply and bool(requests)
    status = "blocked" if apply and not requests and before.estimated_reclaimable_cells else "success"
    return CompactionResult(
        status=status,
        requested_apply=apply,
        applied=changes_applied,
        allow_trim_blank_formatting=allow_trim_blank_formatting,
        requests_planned=len(requests),
        requests_submitted=len(requests) if changes_applied else 0,
        sheets_planned=len(planned_sheet_ids),
        sheets_compacted=len(planned_sheet_ids) if changes_applied else 0,
        cells_reclaimed=cells_reclaimed,
        before=before,
        after=after,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or safely compact Job Market Tracker workbook grid capacity"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--audit",
        action="store_true",
        help="Audit workbook capacity without changing the workbook",
    )
    mode.add_argument(
        "--compact",
        action="store_true",
        help="Build a safe trailing-grid compaction plan",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the compaction plan. Requires --compact",
    )
    parser.add_argument(
        "--allow-trim-blank-formatting",
        action="store_true",
        help=(
            "Explicitly approve removal of formatting-only or formatting-unverified "
            "trailing rows and columns"
        ),
    )
    parser.add_argument(
        "--enforce-critical",
        action="store_true",
        help="Exit with failure when post-run capacity is critical",
    )
    parser.add_argument(
        "--warning-threshold",
        type=float,
        default=DEFAULT_WARNING_THRESHOLD,
    )
    parser.add_argument(
        "--critical-threshold",
        type=float,
        default=DEFAULT_CRITICAL_THRESHOLD,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.apply and not args.compact:
        raise SystemExit("--apply requires --compact")
    if args.allow_trim_blank_formatting and not args.compact:
        raise SystemExit("--allow-trim-blank-formatting requires --compact")
    if not args.audit and not args.compact:
        args.audit = True

    sheet_client = SheetClient.from_settings(load_settings())
    if args.compact:
        result = compact_workbook(
            sheet_client,
            apply=args.apply,
            allow_trim_blank_formatting=args.allow_trim_blank_formatting,
            warning_threshold=args.warning_threshold,
            critical_threshold=args.critical_threshold,
        )
        payload = result.to_dict()
        post_audit = result.after
    else:
        post_audit = audit_workbook(
            sheet_client,
            warning_threshold=args.warning_threshold,
            critical_threshold=args.critical_threshold,
        )
        payload = {
            "run_mode": "workbook_capacity_audit",
            "status": "success",
            **post_audit.to_dict(),
        }
    print(json.dumps(payload, indent=2))
    if args.enforce_critical and post_audit.critical:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
