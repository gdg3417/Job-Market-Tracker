from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.schema as schema_module
from src.models import JOB_FIELDS
from src.schema import HeaderSpec, SchemaValidationError, migrate_trailing_headers


class FakeJobsWorksheet:
    def __init__(self, *, headers: list[str], col_count: int) -> None:
        self.headers = list(headers)
        self.row_count = 1000
        self.col_count = col_count
        self.resize_calls: list[tuple[int, int]] = []
        self.update_calls: list[tuple[str, list[list[str]], str]] = []

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        return list(self.headers)

    def resize(self, *, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))
        self.row_count = rows
        self.col_count = cols

    def update(self, *, range_name: str, values: list[list[str]], value_input_option: str) -> None:
        self.update_calls.append((range_name, values, value_input_option))
        self.headers.extend(values[0])


class FakeWorkbook:
    def fetch_sheet_metadata(self) -> dict:
        return {"properties": {"timeZone": "America/Chicago"}}

    def batch_update(self, request: dict) -> None:
        raise AssertionError(f"Unexpected batch update: {request}")


class FakeSheetClient:
    def __init__(self, worksheet: FakeJobsWorksheet) -> None:
        self.worksheet = worksheet
        self.workbook = FakeWorkbook()
        self._header_cache: dict[str, list[str]] = {}

    def ensure_worksheet(self, worksheet_name: str, *, rows: int, cols: int) -> FakeJobsWorksheet:
        assert worksheet_name == "Jobs"
        return self.worksheet


def test_jobs_migration_expands_only_to_exact_new_canonical_width(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeJobsWorksheet(headers=list(JOB_FIELDS[:-1]), col_count=len(JOB_FIELDS) - 1)
    client = FakeSheetClient(worksheet)
    monkeypatch.setattr(
        schema_module,
        "CANONICAL_SCHEMA",
        {"Jobs": HeaderSpec("Jobs", list(JOB_FIELDS))},
    )

    result = migrate_trailing_headers(client)

    assert result.ok is True
    assert worksheet.resize_calls == [(1000, len(JOB_FIELDS))]
    assert worksheet.update_calls == [("EE1:EE1", [[JOB_FIELDS[-1]]], "USER_ENTERED")]
    detail = result.migration_details[0]
    assert detail["previous_width"] == 134
    assert detail["required_canonical_width"] == 135
    assert detail["final_width"] == 135
    assert detail["headers_appended"] == [JOB_FIELDS[-1]]
    assert detail["out_of_bounds_data_detected"] is False


def test_jobs_migration_rejects_oversized_grid_without_compacting(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeJobsWorksheet(headers=list(JOB_FIELDS), col_count=len(JOB_FIELDS) + 1)
    client = FakeSheetClient(worksheet)
    monkeypatch.setattr(
        schema_module,
        "CANONICAL_SCHEMA",
        {"Jobs": HeaderSpec("Jobs", list(JOB_FIELDS))},
    )
    monkeypatch.setattr(
        schema_module,
        "audit_jobs_integrity",
        lambda _client: SimpleNamespace(
            out_of_bounds_value_count=1,
            out_of_bounds_formula_count=0,
            out_of_bounds_metadata_count=0,
            out_of_bounds_structural_metadata_count=0,
            offending_coordinates=[SimpleNamespace(to_dict=lambda: {"coordinate": "EF10"})],
            to_dict=lambda: {"offending_coordinates": [{"coordinate": "EF10"}]},
        ),
    )

    with pytest.raises(SchemaValidationError, match="EF10"):
        migrate_trailing_headers(client)

    assert worksheet.resize_calls == []
    assert worksheet.update_calls == []


def test_jobs_migration_rejects_blank_oversized_grid_instead_of_preserving_width(monkeypatch: pytest.MonkeyPatch) -> None:
    worksheet = FakeJobsWorksheet(headers=list(JOB_FIELDS), col_count=len(JOB_FIELDS) + 1)
    client = FakeSheetClient(worksheet)
    monkeypatch.setattr(
        schema_module,
        "CANONICAL_SCHEMA",
        {"Jobs": HeaderSpec("Jobs", list(JOB_FIELDS))},
    )
    monkeypatch.setattr(
        schema_module,
        "audit_jobs_integrity",
        lambda _client: SimpleNamespace(
            out_of_bounds_value_count=0,
            out_of_bounds_formula_count=0,
            out_of_bounds_metadata_count=0,
            out_of_bounds_structural_metadata_count=0,
            to_dict=lambda: {"offending_coordinates": []},
        ),
    )

    with pytest.raises(SchemaValidationError, match="exceeds canonical width"):
        migrate_trailing_headers(client)

    assert worksheet.resize_calls == []
