from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from src.models import utc_now_iso
from src.settings import load_settings
from src.sheet_governance_policy import (
    EDITABLE_HEADER_COLOR,
    GENERATED_SURFACE_POLICIES,
    JOBS_CONTROLLED_FIELDS,
    JOBS_EDITABLE_FIELDS,
    SHEET_GUIDE,
    SHEET_POLICIES,
    SYSTEM_HEADER_COLOR,
    SheetPolicy,
    validate_governance_definitions,
)
from src.sheets import SheetClient, with_quota_backoff


@dataclass(slots=True)
class GovernanceResult:
    sheets_governed: int
    sheets_missing: list[str]
    editable_headers: int
    system_headers: int
    dropdowns_applied: int
    filters_applied: int
    freezes_applied: int
    guide_written: bool
    generated_at: str
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["warnings"] = values["warnings"] or []
        return values


def _rgb(value: str) -> dict[str, float]:
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Expected six-digit color hex, got {value!r}")
    return {
        "red": int(text[0:2], 16) / 255,
        "green": int(text[2:4], 16) / 255,
        "blue": int(text[4:6], 16) / 255,
    }


def _ranges(indexes: Iterable[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(indexes))
    if not ordered:
        return []
    output: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index != previous + 1:
            output.append((start, previous + 1))
            start = index
        previous = index
    output.append((start, previous + 1))
    return output


def _grid_range(
    sheet_id: int,
    *,
    start_row: int,
    end_row: int,
    start_column: int,
    end_column: int,
) -> dict[str, int]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_column,
        "endColumnIndex": end_column,
    }


def _header_request(sheet_id: int, policy: SheetPolicy, start: int, end: int, color: str) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": _grid_range(
                sheet_id,
                start_row=policy.header_row - 1,
                end_row=policy.header_row,
                start_column=start,
                end_column=end,
            ),
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _rgb(color),
                    "textFormat": {"bold": True},
                    "wrapStrategy": "WRAP",
                }
            },
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
        }
    }


def _freeze_request(sheet_id: int, policy: SheetPolicy) -> dict[str, Any]:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": policy.frozen_rows,
                    "frozenColumnCount": policy.frozen_columns,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    }


def _filter_request(sheet_id: int, policy: SheetPolicy, rows: int, columns: int) -> dict[str, Any]:
    return {
        "setBasicFilter": {
            "filter": {
                "range": _grid_range(
                    sheet_id,
                    start_row=policy.header_row - 1,
                    end_row=max(policy.header_row, rows),
                    start_column=0,
                    end_column=max(1, columns),
                )
            }
        }
    }


def _dropdown_request(
    sheet_id: int,
    policy: SheetPolicy,
    rows: int,
    column: int,
    options: Iterable[str],
) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": _grid_range(
                sheet_id,
                start_row=policy.header_row,
                end_row=max(policy.header_row + 1, rows),
                start_column=column,
                end_column=column + 1,
            ),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": str(option)} for option in options],
                },
                "strict": True,
                "showCustomUi": True,
            },
        }
    }


def build_sheet_requests(
    *,
    sheet_id: int,
    headers: list[str],
    row_count: int,
    policy: SheetPolicy,
) -> tuple[list[dict[str, Any]], int, int, int, int, int]:
    if not headers:
        return [], 0, 0, 0, 0, 0
    requests = [
        _header_request(sheet_id, policy, 0, len(headers), SYSTEM_HEADER_COLOR),
        _freeze_request(sheet_id, policy),
    ]
    editable_indexes = [index for index, header in enumerate(headers) if policy.is_editable(header)]
    requests.extend(
        _header_request(sheet_id, policy, start, end, EDITABLE_HEADER_COLOR)
        for start, end in _ranges(editable_indexes)
    )
    filters = int(policy.filter_enabled)
    if filters:
        requests.append(_filter_request(sheet_id, policy, row_count, len(headers)))
    dropdowns = 0
    header_indexes = {header: index for index, header in enumerate(headers)}
    for field, options in policy.dropdowns().items():
        if field in header_indexes:
            requests.append(
                _dropdown_request(sheet_id, policy, row_count, header_indexes[field], options)
            )
            dropdowns += 1
    editable = len(editable_indexes)
    return requests, editable, len(headers) - editable, dropdowns, filters, 1


def _worksheet_id(worksheet: Any) -> int:
    worksheet_id = getattr(worksheet, "id", None)
    if worksheet_id is None:
        raise ValueError(f"Worksheet {getattr(worksheet, 'title', '<unknown>')} has no sheet id")
    return int(worksheet_id)


def _guide_values() -> list[list[str]]:
    values = [
        ["Job Market Tracker Sheet Guide", "", "", ""],
        ["", "", "", ""],
        ["Header Color", "Meaning", "Where to edit", "Behavior"],
        ["Green", "Safe for manual edits", "Jobs and configuration tabs", "Controlled fields have dropdowns"],
        ["Gray", "System-managed or generated", "Do not edit directly", "Use green Jobs fields for manual changes"],
        ["", "", "", ""],
        ["Sheet", "Edit mode", "Purpose", "Notes"],
    ]
    for name, policy in SHEET_POLICIES.items():
        mode = "Editable" if policy.all_headers_editable else "Mixed" if policy.editable_fields or policy.dropdowns() else "Read-only"
        note = "All headers are green." if mode == "Editable" else "Edit green headers only." if mode == "Mixed" else "Headers are gray."
        values.append([name, mode, policy.purpose, note])
    return values


def _guide_requests(sheet_id: int, rows: int) -> list[dict[str, Any]]:
    policy = SheetPolicy(SHEET_GUIDE, frozen_rows=3, frozen_columns=1, filter_enabled=False)
    requests = [_freeze_request(sheet_id, policy)]
    for row in (1, 3, 7):
        requests.append(_header_request(sheet_id, SheetPolicy(SHEET_GUIDE, header_row=row), 0, 4, SYSTEM_HEADER_COLOR))
    for row, color in ((4, EDITABLE_HEADER_COLOR), (5, SYSTEM_HEADER_COLOR)):
        requests.append(_header_request(sheet_id, SheetPolicy(SHEET_GUIDE, header_row=row), 0, 1, color))
    requests.append(
        {
            "repeatCell": {
                "range": _grid_range(sheet_id, start_row=0, end_row=rows, start_column=0, end_column=4),
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        }
    )
    for start, end, width in ((0, 1, 150), (1, 4, 310)):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": start,
                        "endIndex": end,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )
    return requests


def write_sheet_guide(sheet_client: SheetClient) -> tuple[Any, int]:
    values = _guide_values()
    worksheet = sheet_client.ensure_worksheet(SHEET_GUIDE, rows=max(100, len(values) + 10), cols=4)
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {SHEET_GUIDE}")
    with_quota_backoff(
        lambda: worksheet.update(
            range_name=f"A1:D{len(values)}",
            values=values,
            value_input_option="USER_ENTERED",
        ),
        operation_name=f"write worksheet {SHEET_GUIDE}",
    )
    return worksheet, len(values)


def apply_sheet_governance(sheet_client: SheetClient) -> GovernanceResult:
    validation = validate_governance_definitions()
    if not validation.ok:
        raise ValueError("Invalid sheet governance definitions: " + "; ".join(validation.errors))
    missing: list[str] = []
    warnings: list[str] = []
    requests: list[dict[str, Any]] = []
    counts = {"sheets": 0, "editable": 0, "system": 0, "dropdowns": 0, "filters": 0, "freezes": 0}
    for name, policy in SHEET_POLICIES.items():
        try:
            worksheet = sheet_client.get_worksheet(name)
        except Exception as exc:
            if policy.required or exc.__class__.__name__ != "WorksheetNotFound":
                raise
            missing.append(name)
            warnings.append(f"Skipped missing worksheet {name}: WorksheetNotFound")
            continue
        headers = [
            str(value or "").strip()
            for value in with_quota_backoff(
                lambda worksheet=worksheet, row=policy.header_row: worksheet.row_values(row),
                operation_name=f"read governance headers {name}",
            )
        ]
        while headers and not headers[-1]:
            headers.pop()
        if not headers:
            warnings.append(f"Skipped worksheet {name} because its header row is empty")
            continue
        built = build_sheet_requests(
            sheet_id=_worksheet_id(worksheet),
            headers=headers,
            row_count=int(getattr(worksheet, "row_count", None) or policy.header_row + 1),
            policy=policy,
        )
        requests.extend(built[0])
        for key, value in zip(("editable", "system", "dropdowns", "filters", "freezes"), built[1:]):
            counts[key] += value
        counts["sheets"] += 1
    guide, guide_rows = write_sheet_guide(sheet_client)
    requests.extend(_guide_requests(_worksheet_id(guide), guide_rows))
    with_quota_backoff(
        lambda: sheet_client.workbook.batch_update({"requests": requests}),
        operation_name="apply workbook sheet UX governance",
    )
    return GovernanceResult(
        sheets_governed=counts["sheets"],
        sheets_missing=missing,
        editable_headers=counts["editable"],
        system_headers=counts["system"],
        dropdowns_applied=counts["dropdowns"],
        filters_applied=counts["filters"],
        freezes_applied=counts["freezes"],
        guide_written=True,
        generated_at=utc_now_iso(),
        warnings=warnings,
    )


def run_sheet_governance() -> dict[str, Any]:
    result = apply_sheet_governance(SheetClient.from_settings(load_settings()))
    return {"run_mode": "sprint_46_sheet_ux_governance", "status": "success", **result.to_dict()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Job Market Tracker sheet UX governance")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        result = validate_governance_definitions().to_dict()
        result["status"] = "success" if result["ok"] else "failed"
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            raise SystemExit(1)
        return
    print(json.dumps(run_sheet_governance(), indent=2))


if __name__ == "__main__":
    main()
