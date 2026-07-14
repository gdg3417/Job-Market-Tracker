from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from src import workbook_capacity as _base


_ORIGINAL_AUDIT_SHEET = _base.audit_sheet


def _range_warning(label: str, dimension: str) -> str:
    return (
        f"{label} has no end{dimension}Index; "
        f"{dimension.lower()} compaction is blocked for this sheet"
    )


def _normalize_range(
    grid_range: dict[str, Any],
    *,
    row_count: int,
    column_count: int,
    sheet_id: int,
    label: str,
) -> tuple[dict[str, Any], list[str]]:
    normalized = deepcopy(grid_range)
    normalized.setdefault("sheetId", sheet_id)
    warnings: list[str] = []
    if "endRowIndex" not in normalized:
        normalized["endRowIndex"] = row_count
        warnings.append(_range_warning(label, "Row"))
    if "endColumnIndex" not in normalized:
        normalized["endColumnIndex"] = column_count
        warnings.append(_range_warning(label, "Column"))
    return normalized, warnings


def _normalize_sheet_structures(
    sheet: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    normalized = deepcopy(sheet)
    properties = normalized.get("properties") or {}
    grid = properties.get("gridProperties") or {}
    sheet_id = int(properties.get("sheetId") or 0)
    row_count = max(1, int(grid.get("rowCount") or 1))
    column_count = max(1, int(grid.get("columnCount") or 1))
    warnings: list[str] = []

    for index, grid_range in enumerate(normalized.get("merges") or []):
        value, range_warnings = _normalize_range(
            grid_range,
            row_count=row_count,
            column_count=column_count,
            sheet_id=sheet_id,
            label=f"merge[{index}]",
        )
        normalized["merges"][index] = value
        warnings.extend(range_warnings)

    basic_filter = normalized.get("basicFilter") or {}
    if basic_filter.get("range") is not None:
        basic_filter["range"], range_warnings = _normalize_range(
            basic_filter["range"],
            row_count=row_count,
            column_count=column_count,
            sheet_id=sheet_id,
            label="basicFilter",
        )
        warnings.extend(range_warnings)

    for key in ("filterViews", "bandedRanges", "protectedRanges"):
        for index, value in enumerate(normalized.get(key) or []):
            grid_range = value.get("range")
            if grid_range is None and key != "protectedRanges":
                continue
            value["range"], range_warnings = _normalize_range(
                grid_range or {},
                row_count=row_count,
                column_count=column_count,
                sheet_id=sheet_id,
                label=f"{key}[{index}]",
            )
            warnings.extend(range_warnings)

    for index, rule in enumerate(normalized.get("conditionalFormats") or []):
        for range_index, grid_range in enumerate(rule.get("ranges") or []):
            value, range_warnings = _normalize_range(
                grid_range,
                row_count=row_count,
                column_count=column_count,
                sheet_id=sheet_id,
                label=f"conditionalFormats[{index}].ranges[{range_index}]",
            )
            rule["ranges"][range_index] = value
            warnings.extend(range_warnings)

    return normalized, warnings


def _normalize_named_ranges(
    named_ranges: Iterable[dict[str, Any]],
    *,
    sheet: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized = deepcopy(list(named_ranges))
    properties = sheet.get("properties") or {}
    grid = properties.get("gridProperties") or {}
    sheet_id = int(properties.get("sheetId") or 0)
    row_count = max(1, int(grid.get("rowCount") or 1))
    column_count = max(1, int(grid.get("columnCount") or 1))
    warnings: list[str] = []

    for index, named_range in enumerate(normalized):
        grid_range = named_range.get("range") or {}
        if int(grid_range.get("sheetId") or 0) != sheet_id:
            continue
        named_range["range"], range_warnings = _normalize_range(
            grid_range,
            row_count=row_count,
            column_count=column_count,
            sheet_id=sheet_id,
            label=f"namedRange[{index}]",
        )
        warnings.extend(range_warnings)
    return normalized, warnings


def audit_sheet(
    sheet: dict[str, Any],
    *,
    named_ranges: Iterable[dict[str, Any]] = (),
    formatting_evidence: _base.FormattingEvidence | None = None,
) -> _base.SheetCapacityAudit:
    """Audit one sheet while blocking only the affected compaction dimension.

    Google Sheets omits an end row or column index to represent an open-ended
    range. Sprint 47 treated either omission as a blocker for both dimensions.
    This compatibility layer materializes the omitted end at the current grid
    boundary, so the original safety calculation blocks only that dimension.
    """

    normalized_sheet, sheet_warnings = _normalize_sheet_structures(sheet)
    normalized_named_ranges, named_warnings = _normalize_named_ranges(
        named_ranges,
        sheet=normalized_sheet,
    )
    result = _ORIGINAL_AUDIT_SHEET(
        normalized_sheet,
        named_ranges=normalized_named_ranges,
        formatting_evidence=formatting_evidence,
    )
    for warning in [*sheet_warnings, *named_warnings]:
        if warning not in result.warnings:
            result.warnings.append(warning)
    return result


def _with_dimension_safe_audit(callback):
    previous = _base.audit_sheet
    _base.audit_sheet = audit_sheet
    try:
        return callback()
    finally:
        _base.audit_sheet = previous


def audit_workbook(*args, **kwargs):
    return _with_dimension_safe_audit(lambda: _base.audit_workbook(*args, **kwargs))


def compact_workbook(*args, **kwargs):
    return _with_dimension_safe_audit(lambda: _base.compact_workbook(*args, **kwargs))


def build_compaction_requests(*args, **kwargs):
    return _base.build_compaction_requests(*args, **kwargs)


def main() -> None:
    _with_dimension_safe_audit(_base.main)


if __name__ == "__main__":
    main()
