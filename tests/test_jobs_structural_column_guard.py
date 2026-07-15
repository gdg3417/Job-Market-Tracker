from __future__ import annotations

import pytest

from src.jobs_integrity import (
    JOBS_CANONICAL_COLUMN_COUNT,
    JobsWriteBoundaryError,
    validate_jobs_batch_update_requests,
)

JOBS_SHEET_ID = 44


def _validate(request: dict, *, allow_trailing_column_deletion: bool = False) -> None:
    validate_jobs_batch_update_requests(
        [request],
        jobs_sheet_id=JOBS_SHEET_ID,
        operation_name="structural guard regression",
        allow_trailing_column_deletion=allow_trailing_column_deletion,
    )


def test_canonical_jobs_column_deletion_is_rejected() -> None:
    request = {
        "deleteDimension": {
            "range": {
                "sheetId": JOBS_SHEET_ID,
                "dimension": "COLUMNS",
                "startIndex": 0,
                "endIndex": 1,
            }
        }
    }

    with pytest.raises(JobsWriteBoundaryError, match="cannot be deleted"):
        _validate(request)


def test_canonical_jobs_column_insertion_is_rejected() -> None:
    request = {
        "insertDimension": {
            "range": {
                "sheetId": JOBS_SHEET_ID,
                "dimension": "COLUMNS",
                "startIndex": 10,
                "endIndex": 11,
            },
            "inheritFromBefore": True,
        }
    }

    with pytest.raises(JobsWriteBoundaryError, match="cannot be inserted or moved"):
        _validate(request)


def test_canonical_jobs_column_move_is_rejected() -> None:
    request = {
        "moveDimension": {
            "source": {
                "sheetId": JOBS_SHEET_ID,
                "dimension": "COLUMNS",
                "startIndex": 10,
                "endIndex": 11,
            },
            "destinationIndex": 20,
        }
    }

    with pytest.raises(JobsWriteBoundaryError, match="cannot be inserted or moved"):
        _validate(request)


def test_column_shifting_range_is_rejected() -> None:
    request = {
        "deleteRange": {
            "range": {
                "sheetId": JOBS_SHEET_ID,
                "startRowIndex": 1,
                "endRowIndex": 2,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            "shiftDimension": "COLUMNS",
        }
    }

    with pytest.raises(JobsWriteBoundaryError, match="column-shifting ranges"):
        _validate(request)


def test_jobs_grid_width_cannot_shrink_or_expand() -> None:
    for column_count in (JOBS_CANONICAL_COLUMN_COUNT - 1, JOBS_CANONICAL_COLUMN_COUNT + 1):
        request = {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": JOBS_SHEET_ID,
                    "gridProperties": {"columnCount": column_count},
                },
                "fields": "gridProperties.columnCount",
            }
        }

        with pytest.raises(JobsWriteBoundaryError, match="must remain exactly"):
            _validate(request)


def test_jobs_grid_width_exact_canonical_value_is_allowed() -> None:
    request = {
        "updateSheetProperties": {
            "properties": {
                "sheetId": JOBS_SHEET_ID,
                "gridProperties": {"columnCount": JOBS_CANONICAL_COLUMN_COUNT},
            },
            "fields": "gridProperties.columnCount",
        }
    }

    _validate(request)


def test_trailing_jobs_column_deletion_requires_explicit_approval() -> None:
    request = {
        "deleteDimension": {
            "range": {
                "sheetId": JOBS_SHEET_ID,
                "dimension": "COLUMNS",
                "startIndex": JOBS_CANONICAL_COLUMN_COUNT,
                "endIndex": JOBS_CANONICAL_COLUMN_COUNT + 20,
            }
        }
    }

    with pytest.raises(JobsWriteBoundaryError, match="cannot be deleted"):
        _validate(request)

    _validate(request, allow_trailing_column_deletion=True)


def test_row_dimension_deletion_remains_allowed() -> None:
    request = {
        "deleteDimension": {
            "range": {
                "sheetId": JOBS_SHEET_ID,
                "dimension": "ROWS",
                "startIndex": 500,
                "endIndex": 600,
            }
        }
    }

    _validate(request)
