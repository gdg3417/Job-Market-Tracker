from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from src.models import utc_now_iso
from src.sheets import SheetClient, with_quota_backoff

SURFACE_STATUS_SHEET = "Surface_Status"
SURFACE_STATUS_HEADERS = [
    "surface_name",
    "last_successful_refresh",
    "source_run",
    "rows_written",
    "status",
    "warning_or_error",
    "data_as_of_date",
    "last_attempted_at",
]
SURFACE_ORDER = {
    name: index
    for index, name in enumerate(
        [
            "Review_Queue",
            "Follow_Up_Queue",
            "Weekly_Value",
            "Weekly_Context",
            "Dashboard",
            "Digest",
            "Governance",
        ]
    )
}


@dataclass(slots=True)
class SurfaceOutcome:
    surface_name: str
    status: str
    rows_written: int = 0
    warning_or_error: str = ""
    last_successful_refresh: str = ""
    source_run: str = ""
    data_as_of_date: str = ""
    last_attempted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_existing(sheet_client: SheetClient) -> dict[str, dict[str, Any]]:
    try:
        records = sheet_client.read_records(SURFACE_STATUS_SHEET)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return {}
        raise
    return {
        str(record.get("surface_name") or "").strip(): record
        for record in records
        if str(record.get("surface_name") or "").strip()
    }


def merge_surface_outcomes(
    existing: dict[str, dict[str, Any]],
    outcomes: Iterable[SurfaceOutcome],
    *,
    source_run: str,
    data_as_of_date: str,
    attempted_at: str | None = None,
) -> list[SurfaceOutcome]:
    timestamp = attempted_at or utc_now_iso()
    merged: list[SurfaceOutcome] = []
    for outcome in outcomes:
        prior = existing.get(outcome.surface_name, {})
        outcome.source_run = source_run
        outcome.data_as_of_date = data_as_of_date
        outcome.last_attempted_at = timestamp
        if outcome.status == "success":
            outcome.last_successful_refresh = timestamp
        else:
            outcome.last_successful_refresh = str(
                prior.get("last_successful_refresh") or ""
            )
        merged.append(outcome)
    return sorted(
        merged,
        key=lambda item: (
            SURFACE_ORDER.get(item.surface_name, 999),
            item.surface_name.lower(),
        ),
    )


def _values(outcomes: Iterable[SurfaceOutcome]) -> list[list[Any]]:
    rows = []
    for outcome in outcomes:
        record = outcome.to_dict()
        rows.append([record.get(header, "") for header in SURFACE_STATUS_HEADERS])
    return [SURFACE_STATUS_HEADERS, *rows]


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def write_surface_status(
    sheet_client: SheetClient,
    outcomes: Iterable[SurfaceOutcome],
    *,
    source_run: str,
    data_as_of_date: str,
    attempted_at: str | None = None,
) -> list[SurfaceOutcome]:
    merged = merge_surface_outcomes(
        _read_existing(sheet_client),
        outcomes,
        source_run=source_run,
        data_as_of_date=data_as_of_date,
        attempted_at=attempted_at,
    )
    values = _values(merged)
    worksheet = sheet_client.ensure_worksheet(
        SURFACE_STATUS_SHEET,
        rows=max(100, len(values) + 10),
        cols=len(SURFACE_STATUS_HEADERS),
    )
    with_quota_backoff(
        lambda: worksheet.clear(),
        operation_name=f"clear worksheet {SURFACE_STATUS_SHEET}",
    )
    end_cell = f"{_column_name(len(SURFACE_STATUS_HEADERS))}{len(values)}"
    with_quota_backoff(
        lambda: worksheet.update(
            range_name=f"A1:{end_cell}",
            values=values,
            value_input_option="USER_ENTERED",
        ),
        operation_name=f"write worksheet {SURFACE_STATUS_SHEET}",
    )
    worksheet_id = getattr(worksheet, "id", None)
    if worksheet_id is not None:
        requests = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": int(worksheet_id),
                        "gridProperties": {
                            "frozenRowCount": 1,
                            "frozenColumnCount": 1,
                        },
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": int(worksheet_id),
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(SURFACE_STATUS_HEADERS),
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.72,
                                "green": 0.72,
                                "blue": 0.72,
                            },
                            "textFormat": {"bold": True},
                            "wrapStrategy": "WRAP",
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": int(worksheet_id),
                        "startRowIndex": 1,
                        "endRowIndex": max(2, len(values)),
                        "startColumnIndex": 0,
                        "endColumnIndex": len(SURFACE_STATUS_HEADERS),
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 1,
                                "green": 1,
                                "blue": 1,
                            }
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            },
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": int(worksheet_id),
                            "startRowIndex": 0,
                            "endRowIndex": max(1, len(values)),
                            "startColumnIndex": 0,
                            "endColumnIndex": len(SURFACE_STATUS_HEADERS),
                        }
                    }
                }
            },
        ]
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name=f"format worksheet {SURFACE_STATUS_SHEET}",
        )
    return merged
