from __future__ import annotations

import argparse
import contextlib
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from src.models import JOB_FIELDS
from src.jobs_boundaries import (
    _cell_value,
    _nonempty,
    _observed_type,
    _trim_headers,
    DEFAULT_OFFENDER_LIMIT,
    JOBS_CANONICAL_COLUMN_COUNT,
    JOBS_FINAL_CANONICAL_FIELD,
    JOBS_IDENTITY_FIELDS,
    JOBS_WORKSHEET_NAME,
    JobsIntegrityError,
    JobsWriteBoundaryError,
    classify_observed_jobs_value,
    column_name_to_number,
    column_number_to_name,
    jobs_canonical_end_column,
    jobs_first_out_of_bounds_column,
    serialize_job_record,
    validate_canonical_write_range,
    validate_job_record,
    validate_jobs_a1_range,
    validate_jobs_batch_update_requests,
    validate_jobs_headers,
)

__all__ = [
    "DEFAULT_OFFENDER_LIMIT",
    "JOBS_CANONICAL_COLUMN_COUNT",
    "JOBS_FINAL_CANONICAL_FIELD",
    "JOBS_IDENTITY_FIELDS",
    "JOBS_WORKSHEET_NAME",
    "JobsIntegrityError",
    "JobsWriteBoundaryError",
    "audit_jobs_integrity",
    "assert_jobs_integrity",
    "classify_observed_jobs_value",
    "column_name_to_number",
    "column_number_to_name",
    "jobs_canonical_end_column",
    "jobs_first_out_of_bounds_column",
    "serialize_job_record",
    "validate_canonical_write_range",
    "validate_job_record",
    "validate_jobs_a1_range",
    "validate_jobs_batch_update_requests",
    "validate_jobs_headers",
]


@dataclass(slots=True)
class JobsIntegrityOffender:
    coordinate: str
    signal: str
    observed_value_type: str = ""
    observed_value_category: str = ""
    possible_canonical_field: str = ""
    canonical_row_identity_present: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JobsIntegrityAudit:
    worksheet_name: str
    canonical_header_count: int
    actual_header_count: int
    final_canonical_header: str
    actual_final_header: str
    canonical_end_column: str
    grid_rows: int
    grid_columns: int
    highest_populated_row: int
    highest_populated_column: int
    highest_populated_column_name: str
    out_of_bounds_value_count: int
    out_of_bounds_formula_count: int
    out_of_bounds_metadata_count: int
    out_of_bounds_structural_metadata_count: int
    furthest_offending_column: int
    furthest_offending_column_name: str
    offending_coordinates: list[JobsIntegrityOffender] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    health_status: str = "unsafe"
    writes_allowed: bool = False

    @property
    def healthy(self) -> bool:
        return self.health_status == "healthy" and self.writes_allowed

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["offending_coordinates"] = [item.to_dict() for item in self.offending_coordinates]
        values["healthy"] = self.healthy
        return values


def _with_quota_backoff(operation: Any, *, operation_name: str) -> Any:
    """Reuse the shared Sheets retry policy without contaminating JSON stdout."""
    from src.sheets import with_quota_backoff

    with contextlib.redirect_stdout(sys.stderr):
        return with_quota_backoff(operation, operation_name=operation_name)


def _load_jobs_worksheet(sheet_client: Any) -> Any:
    """Load Jobs with its existing retry policy while keeping stdout JSON-clean."""
    with contextlib.redirect_stdout(sys.stderr):
        return sheet_client.get_worksheet(JOBS_WORKSHEET_NAME)


def _worksheet_values(worksheet: Any, range_name: str) -> list[list[Any]]:
    try:
        return list(worksheet.get_values(range_name=range_name))
    except TypeError:
        try:
            return list(worksheet.get_values(range_name))
        except TypeError:
            return list(worksheet.get_values())


def _fetch_sheet_metadata(workbook: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return dict(workbook.fetch_sheet_metadata(params=dict(params)))
    except TypeError:
        try:
            return dict(workbook.fetch_sheet_metadata(dict(params)))
        except TypeError:
            return dict(workbook.fetch_sheet_metadata())


def _jobs_sheet_metadata(metadata: Mapping[str, Any], *, sheet_id: int | None = None) -> Mapping[str, Any]:
    for sheet in metadata.get("sheets") or []:
        properties = sheet.get("properties") or {}
        if str(properties.get("title") or "") == JOBS_WORKSHEET_NAME:
            return sheet
        if sheet_id is not None and int(properties.get("sheetId") or -1) == sheet_id:
            return sheet
    return {}


def _iter_structural_ranges(
    sheet: Mapping[str, Any],
    named_ranges: Iterable[Mapping[str, Any]],
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    properties = sheet.get("properties") or {}
    sheet_id = int(properties.get("sheetId") or 0)
    for index, grid_range in enumerate(sheet.get("merges") or []):
        yield f"merge[{index}]", grid_range
    basic_filter = sheet.get("basicFilter") or {}
    if isinstance(basic_filter.get("range"), Mapping):
        yield "basicFilter", basic_filter["range"]
    for key in ("filterViews", "bandedRanges", "protectedRanges"):
        for index, value in enumerate(sheet.get(key) or []):
            grid_range = value.get("range")
            if isinstance(grid_range, Mapping):
                yield f"{key}[{index}]", grid_range
            elif key == "protectedRanges":
                yield f"{key}[{index}]", {"sheetId": sheet_id}
    for index, rule in enumerate(sheet.get("conditionalFormats") or []):
        for range_index, grid_range in enumerate(rule.get("ranges") or []):
            yield f"conditionalFormats[{index}].ranges[{range_index}]", grid_range
    for index, value in enumerate(named_ranges):
        grid_range = value.get("range") or {}
        if int(grid_range.get("sheetId") or -1) == sheet_id:
            yield f"namedRange[{index}]", grid_range


def _row_identity_present(
    canonical_values: Sequence[Sequence[Any]],
    row_number: int,
) -> bool:
    if row_number < 1 or row_number > len(canonical_values):
        return False
    row = canonical_values[row_number - 1]
    for field_name in JOBS_IDENTITY_FIELDS:
        index = JOB_FIELDS.index(field_name)
        if index < len(row) and str(row[index] or "").strip():
            return True
    return False


def _audit_jobs_integrity_once(
    sheet_client: Any,
    worksheet: Any,
    *,
    offender_limit: int = DEFAULT_OFFENDER_LIMIT,
) -> JobsIntegrityAudit:
    actual_headers = _trim_headers(worksheet.row_values(1))
    grid_rows = max(1, int(getattr(worksheet, "row_count", 1) or 1))
    grid_columns = max(1, int(getattr(worksheet, "col_count", 1) or 1))
    readable_columns = min(grid_columns, JOBS_CANONICAL_COLUMN_COUNT)
    canonical_range = f"A1:{column_number_to_name(readable_columns)}{grid_rows}"
    canonical_values = _worksheet_values(worksheet, canonical_range)

    highest_populated_row = 0
    highest_populated_column = 0
    for row_index, row in enumerate(canonical_values, start=1):
        for column_index, value in enumerate(row[:JOBS_CANONICAL_COLUMN_COUNT], start=1):
            if _nonempty(value):
                highest_populated_row = max(highest_populated_row, row_index)
                highest_populated_column = max(highest_populated_column, column_index)

    value_count = 0
    formula_count = 0
    highest_oob_populated_column = 0
    metadata_count = 0
    structural_count = 0
    furthest_offending_column = 0
    offenders: list[JobsIntegrityOffender] = []
    warnings: list[str] = []

    sheet_id_raw = getattr(worksheet, "id", None)
    sheet_id = int(sheet_id_raw) if sheet_id_raw is not None else None
    if grid_columns > JOBS_CANONICAL_COLUMN_COUNT:
        end_column = column_number_to_name(grid_columns)
        metadata = _fetch_sheet_metadata(
            sheet_client.workbook,
            {
                "includeGridData": True,
                "ranges": f"{JOBS_WORKSHEET_NAME}!{jobs_first_out_of_bounds_column()}1:{end_column}{grid_rows}",
                "fields": (
                    "namedRanges,"
                    "sheets(properties(sheetId,title,gridProperties),merges,basicFilter,filterViews,"
                    "bandedRanges,protectedRanges,conditionalFormats,charts(position),slicers(position),"
                    "data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue,formattedValue,"
                    "note,dataValidation,hyperlink,textFormatRuns,chipRuns))))"
                ),
            },
        )
        sheet = _jobs_sheet_metadata(metadata, sheet_id=sheet_id)
        for grid_data in sheet.get("data") or []:
            start_row = int(grid_data.get("startRow") or 0)
            start_column = int(grid_data.get("startColumn") or 0)
            for row_offset, row_data in enumerate(grid_data.get("rowData") or []):
                for column_offset, cell in enumerate(row_data.get("values") or []):
                    row_number = start_row + row_offset + 1
                    column_number = start_column + column_offset + 1
                    if column_number <= JOBS_CANONICAL_COLUMN_COUNT:
                        continue
                    entered = cell.get("userEnteredValue")
                    formula = isinstance(entered, Mapping) and "formulaValue" in entered
                    value = _cell_value(cell)
                    has_value = value is not None and not formula
                    has_metadata = any(
                        _nonempty(cell.get(key))
                        for key in ("note", "dataValidation", "hyperlink", "textFormatRuns", "chipRuns")
                    )
                    if not formula and not has_value and not has_metadata:
                        continue
                    furthest_offending_column = max(furthest_offending_column, column_number)
                    coordinate = f"{column_number_to_name(column_number)}{row_number}"
                    row_identity = _row_identity_present(canonical_values, row_number)
                    if formula:
                        formula_count += 1
                        highest_oob_populated_column = max(highest_oob_populated_column, column_number)
                        signal = "formula"
                    elif has_value:
                        value_count += 1
                        highest_oob_populated_column = max(highest_oob_populated_column, column_number)
                        signal = "value"
                    else:
                        signal = "metadata"
                    if has_metadata:
                        metadata_count += 1
                    if len(offenders) < offender_limit:
                        category, possible_field = classify_observed_jobs_value(value)
                        offenders.append(
                            JobsIntegrityOffender(
                                coordinate=coordinate,
                                signal=signal,
                                observed_value_type=_observed_type(value),
                                observed_value_category=category if has_value or formula else "hard cell metadata",
                                possible_canonical_field=possible_field,
                                canonical_row_identity_present=row_identity,
                            )
                        )

        for label, grid_range in _iter_structural_ranges(sheet, metadata.get("namedRanges") or []):
            end_column_index = grid_range.get("endColumnIndex")
            extends_beyond = end_column_index is None or int(end_column_index or 0) > JOBS_CANONICAL_COLUMN_COUNT
            if not extends_beyond:
                continue
            structural_count += 1
            end_column_number = int(end_column_index or grid_columns)
            furthest_offending_column = max(furthest_offending_column, end_column_number)
            if len(offenders) < offender_limit:
                offenders.append(
                    JobsIntegrityOffender(
                        coordinate=label,
                        signal="structural_metadata",
                        observed_value_type="metadata",
                        observed_value_category="range extends beyond canonical Jobs width",
                    )
                )

        for object_type in ("charts", "slicers"):
            for index, value in enumerate(sheet.get(object_type) or []):
                anchor = (((value.get("position") or {}).get("overlayPosition") or {}).get("anchorCell") or {})
                column_number = int(anchor.get("columnIndex") or 0) + 1 if anchor else 0
                if column_number <= JOBS_CANONICAL_COLUMN_COUNT:
                    continue
                structural_count += 1
                furthest_offending_column = max(furthest_offending_column, column_number)
                if len(offenders) < offender_limit:
                    offenders.append(
                        JobsIntegrityOffender(
                            coordinate=f"{object_type}[{index}]",
                            signal="structural_metadata",
                            observed_value_type="metadata",
                            observed_value_category="object anchored beyond canonical Jobs width",
                        )
                    )

    header_ok = actual_headers == list(JOB_FIELDS)
    width_ok = grid_columns == JOBS_CANONICAL_COLUMN_COUNT
    out_of_bounds_ok = value_count == 0 and formula_count == 0 and metadata_count == 0 and structural_count == 0
    final_header = actual_headers[-1] if actual_headers else ""
    final_header_ok = final_header == JOBS_FINAL_CANONICAL_FIELD
    healthy = header_ok and width_ok and out_of_bounds_ok and final_header_ok

    if not header_ok:
        warnings.append("Jobs headers do not exactly match the canonical schema")
    if not width_ok:
        warnings.append(
            f"Jobs grid width is {grid_columns}; approved width is {JOBS_CANONICAL_COLUMN_COUNT} ({jobs_canonical_end_column()})"
        )
    if not out_of_bounds_ok:
        warnings.append(
            f"Jobs contains out-of-bounds evidence after {jobs_canonical_end_column()}: "
            f"values={value_count}, formulas={formula_count}, hard_metadata={metadata_count}, structural_metadata={structural_count}"
        )

    highest_all_populated = max(highest_populated_column, highest_oob_populated_column)
    furthest_name = column_number_to_name(furthest_offending_column) if furthest_offending_column else ""
    highest_name = column_number_to_name(highest_all_populated) if highest_all_populated else ""
    return JobsIntegrityAudit(
        worksheet_name=JOBS_WORKSHEET_NAME,
        canonical_header_count=JOBS_CANONICAL_COLUMN_COUNT,
        actual_header_count=len(actual_headers),
        final_canonical_header=JOBS_FINAL_CANONICAL_FIELD,
        actual_final_header=final_header,
        canonical_end_column=jobs_canonical_end_column(),
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        highest_populated_row=highest_populated_row,
        highest_populated_column=highest_all_populated,
        highest_populated_column_name=highest_name,
        out_of_bounds_value_count=value_count,
        out_of_bounds_formula_count=formula_count,
        out_of_bounds_metadata_count=metadata_count,
        out_of_bounds_structural_metadata_count=structural_count,
        furthest_offending_column=furthest_offending_column,
        furthest_offending_column_name=furthest_name,
        offending_coordinates=offenders,
        warnings=warnings,
        health_status="healthy" if healthy else "unsafe",
        writes_allowed=healthy,
    )


def audit_jobs_integrity(
    sheet_client: Any,
    *,
    offender_limit: int = DEFAULT_OFFENDER_LIMIT,
) -> JobsIntegrityAudit:
    worksheet = _load_jobs_worksheet(sheet_client)
    return _with_quota_backoff(
        lambda: _audit_jobs_integrity_once(
            sheet_client,
            worksheet,
            offender_limit=offender_limit,
        ),
        operation_name="audit Jobs integrity",
    )


def assert_jobs_integrity(
    sheet_client: Any,
    *,
    phase: str = "Jobs integrity check",
) -> JobsIntegrityAudit:
    audit = audit_jobs_integrity(sheet_client)
    if not audit.healthy:
        coordinates = ", ".join(item.coordinate for item in audit.offending_coordinates[:DEFAULT_OFFENDER_LIMIT]) or "none"
        raise JobsIntegrityError(
            f"{phase} failed; status={audit.health_status}; grid_columns={audit.grid_columns}; "
            f"canonical_columns={audit.canonical_header_count}; out_of_bounds_values={audit.out_of_bounds_value_count}; "
            f"out_of_bounds_formulas={audit.out_of_bounds_formula_count}; "
            f"out_of_bounds_metadata={audit.out_of_bounds_metadata_count}; "
            f"out_of_bounds_structural_metadata={audit.out_of_bounds_structural_metadata_count}; "
            f"first_offenders={coordinates}"
        )
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Jobs worksheet boundary integrity")
    parser.add_argument("--audit", action="store_true", help="Run the read-only Jobs integrity audit")
    parser.add_argument("--enforce", action="store_true", help="Exit nonzero when the Jobs worksheet is unsafe")
    return parser.parse_args()


def _load_sheet_client() -> Any:
    from src.settings import load_settings
    from src.sheets import SheetClient

    with contextlib.redirect_stdout(sys.stderr):
        return SheetClient.from_settings(load_settings())


def main() -> None:
    args = parse_args()
    if not args.audit:
        args.audit = True
    audit = audit_jobs_integrity(_load_sheet_client())
    print(json.dumps(audit.to_dict(), indent=2))
    if args.enforce and not audit.healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
