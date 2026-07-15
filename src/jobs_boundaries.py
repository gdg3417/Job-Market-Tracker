from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from src.models import JOB_FIELDS, VALID_MOVE_VALUE_CLASSIFICATIONS

JOBS_WORKSHEET_NAME = "Jobs"
JOBS_CANONICAL_COLUMN_COUNT = len(JOB_FIELDS)
JOBS_FINAL_CANONICAL_FIELD = "decision_evidence_conflict_notes"
JOBS_IDENTITY_FIELDS = ("job_key", "company", "title", "canonical_url", "source_job_id")
DEFAULT_OFFENDER_LIMIT = 10


class JobsIntegrityError(ValueError):
    """Raised when the Jobs worksheet is not safe to read or mutate."""


class JobsWriteBoundaryError(JobsIntegrityError):
    """Raised when a proposed Jobs write crosses the canonical boundary."""


def column_number_to_name(number: int) -> str:
    if number < 1:
        raise ValueError("Column numbers are one-based and must be positive")
    value = ""
    current = number
    while current:
        current, remainder = divmod(current - 1, 26)
        value = chr(65 + remainder) + value
    return value


def column_name_to_number(name: str) -> int:
    text = str(name or "").strip().upper().replace("$", "")
    if not text or not text.isalpha():
        raise ValueError(f"Invalid column name: {name!r}")
    result = 0
    for character in text:
        result = result * 26 + ord(character) - 64
    return result


def jobs_canonical_end_column() -> str:
    return column_number_to_name(JOBS_CANONICAL_COLUMN_COUNT)


def jobs_first_out_of_bounds_column() -> str:
    return column_number_to_name(JOBS_CANONICAL_COLUMN_COUNT + 1)


def _trim_headers(values: Iterable[Any]) -> list[str]:
    headers = [str(value or "").strip() for value in values]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def validate_jobs_headers(actual_headers: Iterable[Any]) -> list[str]:
    headers = _trim_headers(actual_headers)
    expected = list(JOB_FIELDS)
    if headers == expected:
        return headers

    missing = [header for header in expected if header not in headers]
    extras = [header for header in headers if header not in expected]
    order_mismatch = not missing and not extras and headers != expected
    details: list[str] = []
    if len(headers) != JOBS_CANONICAL_COLUMN_COUNT:
        details.append(
            f"header count {len(headers)} does not equal canonical count {JOBS_CANONICAL_COLUMN_COUNT}"
        )
    if missing:
        details.append("missing headers: " + ", ".join(missing[:10]))
    if extras:
        details.append("unexpected headers: " + ", ".join(extras[:10]))
    if order_mismatch:
        details.append("canonical header order is not preserved")
    raise JobsIntegrityError("Jobs header validation failed: " + "; ".join(details))


def validate_job_record(record: Mapping[str, Any]) -> None:
    keys = list(record.keys())
    unknown = [key for key in keys if key not in JOB_FIELDS]
    missing = [field_name for field_name in JOB_FIELDS if field_name not in record]
    if unknown or missing:
        details: list[str] = []
        if unknown:
            details.append("unknown fields: " + ", ".join(str(value) for value in unknown[:10]))
        if missing:
            details.append("missing canonical fields: " + ", ".join(missing[:10]))
        raise JobsWriteBoundaryError(
            "Jobs record does not match the canonical structure: " + "; ".join(details)
        )


def serialize_job_record(record: Mapping[str, Any]) -> list[Any]:
    validate_job_record(record)
    row = [record.get(field_name, "") for field_name in JOB_FIELDS]
    if len(row) != JOBS_CANONICAL_COLUMN_COUNT:
        raise JobsWriteBoundaryError(
            f"Serialized Jobs row width {len(row)} does not equal {JOBS_CANONICAL_COLUMN_COUNT}"
        )
    return row


def validate_canonical_write_range(
    worksheet_name: str,
    start_row: int,
    start_column: int,
    row_count: int,
    column_count: int,
    *,
    operation_name: str = "unspecified Jobs write",
    proposed_range: str = "",
) -> None:
    if worksheet_name != JOBS_WORKSHEET_NAME:
        return
    end_row = start_row + row_count - 1
    end_column = start_column + column_count - 1
    canonical_end = jobs_canonical_end_column()
    display_range = proposed_range or (
        f"{column_number_to_name(max(start_column, 1))}{max(start_row, 1)}:"
        f"{column_number_to_name(max(end_column, 1))}{max(end_row, 1)}"
    )
    invalid = (
        start_row < 1
        or start_column < 1
        or row_count < 1
        or column_count < 1
        or end_column > JOBS_CANONICAL_COLUMN_COUNT
    )
    if invalid:
        raise JobsWriteBoundaryError(
            "Jobs write rejected; "
            f"worksheet={worksheet_name}; proposed_range={display_range}; "
            f"start_row={start_row}; end_row={end_row}; "
            f"start_column={start_column}; end_column={end_column}; "
            f"canonical_maximum={canonical_end} ({JOBS_CANONICAL_COLUMN_COUNT}); "
            f"operation={operation_name}"
        )


_A1_CELL_RE = re.compile(r"^\$?([A-Za-z]+)(?:\$?(\d+))?$")


def _split_a1_range(range_name: str) -> tuple[str | None, str, str]:
    text = str(range_name or "").strip()
    worksheet_name: str | None = None
    if "!" in text:
        worksheet_part, text = text.rsplit("!", 1)
        worksheet_name = worksheet_part.strip().strip("'").replace("''", "'")
    if ":" in text:
        start, end = text.split(":", 1)
    else:
        start = end = text
    return worksheet_name, start.strip(), end.strip()


def _parse_a1_cell(cell: str) -> tuple[int, int | None]:
    match = _A1_CELL_RE.fullmatch(cell)
    if not match:
        raise JobsWriteBoundaryError(f"Unsupported or malformed A1 coordinate: {cell!r}")
    column = column_name_to_number(match.group(1))
    row = int(match.group(2)) if match.group(2) else None
    return column, row


def validate_jobs_a1_range(
    range_name: str,
    *,
    operation_name: str,
    require_explicit_rows: bool = True,
) -> tuple[int, int, int | None, int | None]:
    worksheet_name, start_cell, end_cell = _split_a1_range(range_name)
    if worksheet_name is not None and worksheet_name != JOBS_WORKSHEET_NAME:
        return 0, 0, None, None
    start_column, start_row = _parse_a1_cell(start_cell)
    end_column, end_row = _parse_a1_cell(end_cell)
    if require_explicit_rows and (start_row is None or end_row is None):
        raise JobsWriteBoundaryError(
            "Jobs write rejected because explicit row bounds are required; "
            f"proposed_range={range_name}; operation={operation_name}"
        )
    validate_canonical_write_range(
        JOBS_WORKSHEET_NAME,
        start_row or 1,
        start_column,
        (end_row or start_row or 1) - (start_row or 1) + 1,
        end_column - start_column + 1,
        operation_name=operation_name,
        proposed_range=range_name,
    )
    return start_column, end_column, start_row, end_row


def _range_for_request(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if "range" in value and isinstance(value.get("range"), Mapping):
        return value.get("range")
    filter_value = value.get("filter")
    if isinstance(filter_value, Mapping) and isinstance(filter_value.get("range"), Mapping):
        return filter_value.get("range")
    source = value.get("source")
    if isinstance(source, Mapping):
        return source
    return None


def _request_column_bounds(
    request_type: str,
    payload: Mapping[str, Any],
) -> tuple[int, int] | None:
    grid_range = _range_for_request(payload)
    if grid_range is not None:
        if (
            request_type
            in {
                "updateDimensionProperties",
                "deleteDimension",
                "insertDimension",
                "autoResizeDimensions",
                "moveDimension",
            }
            and str(grid_range.get("dimension") or "").upper() == "ROWS"
        ):
            return None
        start = int(grid_range.get("startColumnIndex") or 0)
        end = grid_range.get("endColumnIndex")
        if end is None:
            return start, JOBS_CANONICAL_COLUMN_COUNT + 1
        return start, int(end)

    start = payload.get("start")
    if isinstance(start, Mapping):
        start_column = int(start.get("columnIndex") or 0)
        rows = payload.get("rows") or []
        width = max(
            (len(row.get("values") or []) for row in rows if isinstance(row, Mapping)),
            default=1,
        )
        return start_column, start_column + width

    if request_type == "updateSheetProperties":
        properties = payload.get("properties") or {}
        grid = properties.get("gridProperties") or {}
        if "columnCount" in grid:
            return 0, int(grid.get("columnCount") or 0)
    return None


def _request_sheet_id(request_type: str, payload: Mapping[str, Any]) -> int | None:
    grid_range = _range_for_request(payload)
    if grid_range is not None and "sheetId" in grid_range:
        return int(grid_range.get("sheetId") or 0)
    start = payload.get("start")
    if isinstance(start, Mapping) and "sheetId" in start:
        return int(start.get("sheetId") or 0)
    properties = payload.get("properties")
    if isinstance(properties, Mapping) and "sheetId" in properties:
        return int(properties.get("sheetId") or 0)
    if "sheetId" in payload:
        return int(payload.get("sheetId") or 0)
    return None


def _reject_jobs_direct_request(
    *,
    request_type: str,
    request_index: int,
    operation_name: str,
    reason: str,
) -> None:
    raise JobsWriteBoundaryError(
        "Jobs direct API request rejected; "
        f"request={request_type}; request_index={request_index}; reason={reason}; "
        f"canonical_maximum={jobs_canonical_end_column()} ({JOBS_CANONICAL_COLUMN_COUNT}); "
        f"operation={operation_name}"
    )


def validate_jobs_batch_update_requests(
    requests: Sequence[Mapping[str, Any]],
    *,
    jobs_sheet_id: int,
    operation_name: str,
    allow_trailing_column_deletion: bool = False,
) -> None:
    for request_index, request in enumerate(requests):
        if not isinstance(request, Mapping) or len(request) != 1:
            continue
        request_type, raw_payload = next(iter(request.items()))
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        sheet_id = _request_sheet_id(request_type, payload)
        if sheet_id != jobs_sheet_id:
            continue

        if request_type in {"appendCells", "appendDimension"}:
            _reject_jobs_direct_request(
                request_type=request_type,
                request_index=request_index,
                operation_name=operation_name,
                reason="append operations are not permitted on Jobs",
            )

        if request_type == "deleteDimension":
            dimension_range = payload.get("range") or {}
            dimension = str(dimension_range.get("dimension") or "").upper()
            if dimension == "COLUMNS":
                start_index = int(dimension_range.get("startIndex") or 0)
                end_index = int(dimension_range.get("endIndex") or 0)
                if (
                    allow_trailing_column_deletion
                    and start_index >= JOBS_CANONICAL_COLUMN_COUNT
                    and end_index > start_index
                ):
                    continue
                _reject_jobs_direct_request(
                    request_type=request_type,
                    request_index=request_index,
                    operation_name=operation_name,
                    reason="canonical Jobs columns cannot be deleted",
                )

        if request_type in {"insertDimension", "moveDimension"}:
            dimension_range = payload.get("range") or payload.get("source") or {}
            if str(dimension_range.get("dimension") or "").upper() == "COLUMNS":
                _reject_jobs_direct_request(
                    request_type=request_type,
                    request_index=request_index,
                    operation_name=operation_name,
                    reason="canonical Jobs columns cannot be inserted or moved",
                )

        if request_type in {"insertRange", "deleteRange"}:
            shift_dimension = str(payload.get("shiftDimension") or "").upper()
            if shift_dimension == "COLUMNS":
                _reject_jobs_direct_request(
                    request_type=request_type,
                    request_index=request_index,
                    operation_name=operation_name,
                    reason="column-shifting ranges are not permitted on Jobs",
                )

        if request_type == "cutPaste":
            _reject_jobs_direct_request(
                request_type=request_type,
                request_index=request_index,
                operation_name=operation_name,
                reason="cut and paste operations can displace canonical Jobs fields",
            )

        if request_type == "updateSheetProperties":
            properties = payload.get("properties") or {}
            grid = properties.get("gridProperties") or {}
            if "columnCount" in grid:
                requested_column_count = int(grid.get("columnCount") or 0)
                if requested_column_count != JOBS_CANONICAL_COLUMN_COUNT:
                    _reject_jobs_direct_request(
                        request_type=request_type,
                        request_index=request_index,
                        operation_name=operation_name,
                        reason=(
                            "Jobs grid columnCount must remain exactly "
                            f"{JOBS_CANONICAL_COLUMN_COUNT}"
                        ),
                    )

        bounds = _request_column_bounds(request_type, payload)
        if bounds is None:
            continue
        start_zero_based, end_zero_based_exclusive = bounds
        if start_zero_based < 0 or end_zero_based_exclusive > JOBS_CANONICAL_COLUMN_COUNT:
            start_one_based = start_zero_based + 1
            end_one_based = end_zero_based_exclusive
            raise JobsWriteBoundaryError(
                "Jobs direct API request rejected; "
                f"request={request_type}; request_index={request_index}; "
                f"start_column={start_one_based}; end_column={end_one_based}; "
                f"canonical_maximum={jobs_canonical_end_column()} ({JOBS_CANONICAL_COLUMN_COUNT}); "
                f"operation={operation_name}"
            )


def _nonempty(value: Any) -> bool:
    if value is None or value == "" or value is False:
        return False
    if isinstance(value, Mapping):
        return any(_nonempty(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_nonempty(item) for item in value)
    return True


def _cell_value(cell: Mapping[str, Any]) -> Any:
    entered = cell.get("userEnteredValue")
    if isinstance(entered, Mapping):
        for key in ("stringValue", "numberValue", "boolValue", "formulaValue", "errorValue"):
            if key in entered:
                return entered.get(key)
    effective = cell.get("effectiveValue")
    if isinstance(effective, Mapping):
        for key in ("stringValue", "numberValue", "boolValue", "errorValue"):
            if key in effective:
                return effective.get(key)
    if cell.get("formattedValue") not in (None, ""):
        return cell.get("formattedValue")
    return None


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


def classify_observed_jobs_value(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if text in VALID_MOVE_VALUE_CLASSIFICATIONS:
        return "recognized controlled value", "move_value_classification"
    return "unrecognized value", ""
