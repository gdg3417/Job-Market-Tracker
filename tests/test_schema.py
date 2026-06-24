from __future__ import annotations

import pytest

import src.schema as schema_module
from src.schema import (
    CANONICAL_SCHEMA,
    DIGEST_HEADERS,
    EXPECTED_TIMEZONE,
    RUNS_HEADERS,
    HeaderSpec,
    SchemaValidationError,
    compare_headers,
    migrate_trailing_headers,
    validate_record_headers_for_write,
)


class _FakeWorksheet:
    def __init__(self) -> None:
        self.row_count = 1000
        self.col_count = 2
        self.headers = ["a", "b"]
        self.row_values_calls = 0
        self.resize_calls: list[tuple[int, int]] = []
        self.update_calls: list[tuple[str, list[list[str]], str]] = []

    def resize(self, *, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))
        self.row_count = rows
        self.col_count = cols

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        self.row_values_calls += 1
        return list(self.headers)

    def update(self, *, range_name: str, values: list[list[str]], value_input_option: str) -> None:
        self.update_calls.append((range_name, values, value_input_option))
        self.headers.extend(values[0])


class _FakeWorkbook:
    def fetch_sheet_metadata(self) -> dict:
        return {"properties": {"timeZone": EXPECTED_TIMEZONE}}

    def batch_update(self, request: dict) -> None:
        raise AssertionError(f"Unexpected batch update: {request}")


class _FakeSheetClient:
    def __init__(self, worksheet: _FakeWorksheet) -> None:
        self.worksheet = worksheet
        self.workbook = _FakeWorkbook()
        self._header_cache = {"Example": ["a", "b"]}

    def ensure_worksheet(self, worksheet_name: str, *, rows: int, cols: int) -> _FakeWorksheet:
        assert worksheet_name == "Example"
        return self.worksheet


def test_runs_schema_contains_full_richer_run_record_shape():
    assert CANONICAL_SCHEMA["Runs"].headers == RUNS_HEADERS
    assert RUNS_HEADERS == [
        "run_id",
        "run_type",
        "source_type",
        "source_name",
        "status",
        "started_at",
        "finished_at",
        "duration_seconds",
        "records_found",
        "records_inserted",
        "records_updated",
        "records_failed",
        "rows_read",
        "config_companies_rows",
        "config_searches_rows",
        "companies_read",
        "searches_read",
        "error_message",
        "notes",
        "created_at",
        "updated_at",
    ]


def test_digest_schema_uses_header_row_five_and_appends_sprint26_fields():
    spec = CANONICAL_SCHEMA["Digest"]

    assert spec.header_row == 5
    assert spec.headers == DIGEST_HEADERS
    assert spec.headers[0] == "digest_section"
    assert spec.headers[18] == "score_explanation"
    assert spec.headers[-7:] == [
        "potential_priority_score",
        "potential_priority",
        "evidence_completeness_score",
        "score_status",
        "verified_total_score",
        "verified_alert_tier",
        "enrichment_status",
    ]


def test_expected_timezone_is_central():
    assert EXPECTED_TIMEZONE == "America/Chicago"


def test_compare_headers_reports_missing_extra_and_order_differences():
    missing_result = compare_headers(HeaderSpec("Example", ["a", "b", "c"]), ["a", "b"])
    extra_result = compare_headers(HeaderSpec("Example", ["a", "b"]), ["a", "b", "legacy"])
    order_result = compare_headers(HeaderSpec("Example", ["a", "b", "c"]), ["a", "c", "b"])

    assert missing_result.missing_headers == ["c"]
    assert not missing_result.ok
    assert extra_result.extra_headers == ["legacy"]
    assert not extra_result.ok
    assert order_result.order_difference is True
    assert not order_result.ok


def test_validate_record_headers_for_write_rejects_missing_required_sheet_headers():
    with pytest.raises(SchemaValidationError, match="missing required headers"):
        validate_record_headers_for_write("Runs", ["run_id", "status"], {"run_id": "abc", "status": "success"})


def test_validate_record_headers_for_write_rejects_unknown_record_keys():
    with pytest.raises(SchemaValidationError, match="not present in the header row"):
        validate_record_headers_for_write("Scratch", ["run_id", "status"], {"run_id": "abc", "other_key": "x"})


def test_validate_record_headers_for_write_accepts_partial_record_when_headers_are_canonical():
    validate_record_headers_for_write("Runs", RUNS_HEADERS, {"run_id": "abc", "status": "success"})


def test_migrate_trailing_headers_expands_grid_before_writing(monkeypatch: pytest.MonkeyPatch):
    worksheet = _FakeWorksheet()
    sheet_client = _FakeSheetClient(worksheet)
    monkeypatch.setattr(
        schema_module,
        "CANONICAL_SCHEMA",
        {"Example": HeaderSpec("Example", ["a", "b", "c"])},
    )

    result = migrate_trailing_headers(sheet_client)

    assert result.ok is True
    assert result.timezone == EXPECTED_TIMEZONE
    assert result.sheets[0].actual_headers == ["a", "b", "c"]
    assert worksheet.row_values_calls == 1
    assert worksheet.resize_calls == [(1000, 3)]
    assert worksheet.update_calls == [("C1:C1", [["c"]], "USER_ENTERED")]
    assert worksheet.headers == ["a", "b", "c"]
    assert sheet_client._header_cache == {}
