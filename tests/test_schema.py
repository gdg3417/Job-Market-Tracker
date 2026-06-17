from __future__ import annotations

import pytest

from src.schema import (
    CANONICAL_SCHEMA,
    DIGEST_HEADERS,
    EXPECTED_TIMEZONE,
    RUNS_HEADERS,
    HeaderSpec,
    SchemaValidationError,
    compare_headers,
    validate_record_headers_for_write,
)


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


def test_digest_schema_uses_sprint_11_digest_header_row():
    spec = CANONICAL_SCHEMA["Digest"]

    assert spec.header_row == 5
    assert spec.headers == DIGEST_HEADERS
    assert spec.headers[0] == "digest_section"
    assert spec.headers[-1] == "score_explanation"


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
