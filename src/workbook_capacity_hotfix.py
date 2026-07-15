from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from src import workbook_capacity as _base
from src.jobs_boundaries import (
    JOBS_CANONICAL_COLUMN_COUNT,
    JOBS_IDENTITY_FIELDS,
    JOBS_WORKSHEET_NAME,
    classify_observed_jobs_value,
    column_number_to_name,
    validate_jobs_batch_update_requests,
)


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


def _entered_value(cell: Mapping[str, Any]) -> Any:
    entered = cell.get("userEnteredValue")
    if isinstance(entered, Mapping):
        for key in ("stringValue", "numberValue", "boolValue", "formulaValue"):
            if key in entered:
                return entered.get(key)
    effective = cell.get("effectiveValue")
    if isinstance(effective, Mapping):
        for key in ("stringValue", "numberValue", "boolValue"):
            if key in effective:
                return effective.get(key)
    return cell.get("formattedValue")


def _observed_type(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _canonical_identity_rows(sheet: Mapping[str, Any]) -> set[int]:
    identity_indexes = {index for index, field_name in enumerate(_base.CANONICAL_SCHEMA["Jobs"].headers) if field_name in JOBS_IDENTITY_FIELDS}
    rows: set[int] = set()
    for grid_data in sheet.get("data") or []:
        start_row = int(grid_data.get("startRow") or 0)
        start_column = int(grid_data.get("startColumn") or 0)
        for row_offset, row_data in enumerate(grid_data.get("rowData") or []):
            for column_offset, cell in enumerate(row_data.get("values") or []):
                column_index = start_column + column_offset
                if column_index not in identity_indexes:
                    continue
                if "value" in _base._cell_signals(cell):
                    rows.add(start_row + row_offset + 1)
                    break
    return rows


def _jobs_oob_warnings(sheet: Mapping[str, Any], *, limit: int = 10) -> list[str]:
    properties = sheet.get("properties") or {}
    if str(properties.get("title") or "") != JOBS_WORKSHEET_NAME:
        return []
    identity_rows = _canonical_identity_rows(sheet)
    warnings: list[str] = []
    count = 0
    furthest = 0
    for grid_data in sheet.get("data") or []:
        start_row = int(grid_data.get("startRow") or 0)
        start_column = int(grid_data.get("startColumn") or 0)
        for row_offset, row_data in enumerate(grid_data.get("rowData") or []):
            for column_offset, cell in enumerate(row_data.get("values") or []):
                row_number = start_row + row_offset + 1
                column_number = start_column + column_offset + 1
                if column_number <= JOBS_CANONICAL_COLUMN_COUNT:
                    continue
                signals = _base._cell_signals(cell)
                if not signals:
                    continue
                count += 1
                furthest = max(furthest, column_number)
                if len(warnings) >= limit:
                    continue
                value = _entered_value(cell)
                category, possible_field = classify_observed_jobs_value(value)
                warning = (
                    "Out-of-bounds Jobs evidence: "
                    f"coordinate={column_number_to_name(column_number)}{row_number}; "
                    f"observed_value_type={_observed_type(value)}; "
                    f"observed_value_category={category if 'value' in signals else 'hard cell metadata'}; "
                    f"possible_canonical_field={possible_field or 'none'}; "
                    f"canonical_row_identity_present={'true' if row_number in identity_rows else 'false'}"
                )
                warnings.append(warning)
    if count:
        warnings.insert(
            0,
            f"Jobs has {count} populated or hard-metadata cell(s) after EE; furthest offending column={column_number_to_name(furthest)}",
        )
    return warnings


def _apply_jobs_width_policy(
    result: _base.SheetCapacityAudit,
    *,
    formatting_evidence: _base.FormattingEvidence,
) -> None:
    if result.worksheet_name != JOBS_WORKSHEET_NAME:
        return
    hard_boundary = max(
        result.highest_populated_column,
        result.highest_formula_column,
        result.highest_hard_metadata_column,
    )
    if hard_boundary > JOBS_CANONICAL_COLUMN_COUNT:
        result.warnings.append(
            "Jobs compaction preserves the out-of-bounds data or hard-metadata boundary for investigation"
        )
        return
    if result.target_columns > JOBS_CANONICAL_COLUMN_COUNT + _base.DEFAULT_COLUMN_HEADROOM:
        result.warnings.append(
            "Jobs compaction preserves an out-of-bounds structural range for investigation"
        )
        return
    if result.allocated_columns <= JOBS_CANONICAL_COLUMN_COUNT:
        return

    target_columns = JOBS_CANONICAL_COLUMN_COUNT
    result.target_columns = target_columns
    formatted_cells_in_delete_region = {
        (row, column)
        for row, column in formatting_evidence.formatted_cells
        if row > result.target_rows or column > target_columns
    }
    column_delete_possible = target_columns < result.allocated_columns
    column_formatting = any(column > target_columns for _, column in formatting_evidence.formatted_cells)
    result.column_compaction_requires_formatting_approval = column_delete_possible and (
        not formatting_evidence.column_scan_complete or column_formatting
    )
    result.formatting_scan_complete = (
        (result.target_rows >= result.allocated_rows or formatting_evidence.row_scan_complete)
        and (not column_delete_possible or formatting_evidence.column_scan_complete)
    )
    result.blank_formatted_grid_cells_detected = len(formatted_cells_in_delete_region)

    target_cells = result.target_rows * target_columns
    result.estimated_reclaimable_cells = max(0, result.allocated_cells - target_cells)
    no_approval_rows = (
        result.allocated_rows
        if result.row_compaction_requires_formatting_approval
        else result.target_rows
    )
    no_approval_columns = (
        result.allocated_columns
        if result.column_compaction_requires_formatting_approval
        else target_columns
    )
    result.reclaimable_without_formatting_approval = max(
        0,
        result.allocated_cells - (no_approval_rows * no_approval_columns),
    )
    if result.formatting_scan_complete:
        result.truly_unused_grid_cells = max(
            0,
            result.estimated_reclaimable_cells - result.blank_formatted_grid_cells_detected,
        )
        result.formatting_unverified_grid_cells = 0
    else:
        result.truly_unused_grid_cells = 0
        result.formatting_unverified_grid_cells = max(
            0,
            result.estimated_reclaimable_cells - result.blank_formatted_grid_cells_detected,
        )
    result.blank_formatted_or_unverified_grid_cells = (
        result.blank_formatted_grid_cells_detected + result.formatting_unverified_grid_cells
    )
    if result.column_compaction_requires_formatting_approval:
        warning = "Trailing Jobs columns require explicit blank-formatting approval"
        if warning not in result.warnings:
            result.warnings.append(warning)


def audit_sheet(
    sheet: dict[str, Any],
    *,
    named_ranges: Iterable[dict[str, Any]] = (),
    formatting_evidence: _base.FormattingEvidence | None = None,
) -> _base.SheetCapacityAudit:
    """Audit one sheet while preserving data and enforcing the exact Jobs width policy."""

    normalized_sheet, sheet_warnings = _normalize_sheet_structures(sheet)
    normalized_named_ranges, named_warnings = _normalize_named_ranges(
        named_ranges,
        sheet=normalized_sheet,
    )
    evidence = formatting_evidence or _base.FormattingEvidence()
    result = _ORIGINAL_AUDIT_SHEET(
        normalized_sheet,
        named_ranges=normalized_named_ranges,
        formatting_evidence=evidence,
    )
    _apply_jobs_width_policy(result, formatting_evidence=evidence)
    for warning in [*sheet_warnings, *named_warnings, *_jobs_oob_warnings(normalized_sheet)]:
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


def compact_workbook(
    sheet_client,
    *,
    apply: bool,
    allow_trim_blank_formatting: bool = False,
    warning_threshold: float = _base.DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = _base.DEFAULT_CRITICAL_THRESHOLD,
):
    before = audit_workbook(
        sheet_client,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
    )
    requests = _base.build_compaction_requests(
        before,
        allow_trim_blank_formatting=allow_trim_blank_formatting,
    )
    jobs_audit = next((sheet for sheet in before.sheets if sheet.worksheet_name == JOBS_WORKSHEET_NAME), None)
    if jobs_audit is not None:
        validate_jobs_batch_update_requests(
            requests,
            jobs_sheet_id=jobs_audit.sheet_id,
            operation_name="apply workbook capacity compaction",
            allow_trailing_column_deletion=True,
        )
    planned_sheet_ids = {
        int(request["deleteDimension"]["range"]["sheetId"])
        for request in requests
    }
    if apply and requests:
        _base.with_quota_backoff(
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
    return _base.CompactionResult(
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


def build_compaction_requests(*args, **kwargs):
    return _base.build_compaction_requests(*args, **kwargs)


def main() -> None:
    previous_compact = _base.compact_workbook
    _base.compact_workbook = compact_workbook
    try:
        _with_dimension_safe_audit(_base.main)
    finally:
        _base.compact_workbook = previous_compact


if __name__ == "__main__":
    main()
