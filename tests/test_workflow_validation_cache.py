from __future__ import annotations

import json

import pytest

import src.workflow_validation as workflow_validation


def _cached_result() -> dict:
    return {
        "ok": True,
        "timezone": "America/Chicago",
        "expected_timezone": "America/Chicago",
        "timezone_ok": True,
        "sheets": [
            {"worksheet_name": "Jobs", "ok": True},
            {"worksheet_name": "Runs", "ok": True},
            {"worksheet_name": "Enrichment_Queue", "ok": True},
        ],
    }


def test_cached_validation_summary_reads_prior_schema_output(tmp_path, monkeypatch):
    output = "Sheets API quota retry message\n" + json.dumps(_cached_result())
    (tmp_path / "schema_validation.json").write_text(output, encoding="utf-8")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    summary = workflow_validation._cached_validation_summary()

    assert summary == {
        "ok": True,
        "timezone": "America/Chicago",
        "expected_timezone": "America/Chicago",
        "timezone_ok": True,
        "worksheets_validated": 3,
        "worksheet_names": ["Jobs", "Runs", "Enrichment_Queue"],
    }


def test_workflow_validation_uses_cached_result_without_second_schema_read(tmp_path, monkeypatch):
    (tmp_path / "schema_validation.json").write_text(json.dumps(_cached_result()), encoding="utf-8")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    appended: list[dict] = []

    class FakeSheetClient:
        def append_run(self, record: dict) -> None:
            appended.append(record)

    def fail_live_validation(_client):
        raise AssertionError("live workbook validation should not run when cached output exists")

    monkeypatch.setattr(workflow_validation, "load_settings", lambda: object())
    monkeypatch.setattr(workflow_validation.SheetClient, "from_settings", lambda _settings: FakeSheetClient())
    monkeypatch.setattr(workflow_validation, "validate_workbook_or_raise", fail_live_validation)

    result = workflow_validation.run_workflow_validation()

    assert result["status"] == "success"
    assert result["validation_source"] == "cached"
    assert result["worksheets_validated"] == 3
    assert len(appended) == 1
    assert appended[0]["records_found"] == 3


def test_cached_validation_summary_rejects_failed_schema_result(tmp_path, monkeypatch):
    failed = _cached_result() | {"ok": False}
    (tmp_path / "schema_validation.json").write_text(json.dumps(failed), encoding="utf-8")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    with pytest.raises(ValueError, match="did not pass"):
        workflow_validation._cached_validation_summary()
