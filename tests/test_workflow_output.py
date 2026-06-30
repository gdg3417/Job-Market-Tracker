from __future__ import annotations

import json

from src.workflow_output import load_json_output


def test_load_json_output_reads_clean_json(tmp_path):
    path = tmp_path / "command-output.txt"
    path.write_text(json.dumps({"status": "success", "records": 3}) + "\n", encoding="utf-8")

    assert load_json_output(path) == {"status": "success", "records": 3}


def test_load_json_output_reads_final_pretty_json_after_log_lines(tmp_path):
    path = tmp_path / "command-output.txt"
    path.write_text(
        "Sheets API quota hit during read; waiting 65 seconds.\n"
        + json.dumps({"status": "success", "failed_messages": 0}, indent=2),
        encoding="utf-8",
    )

    assert load_json_output(path, {"status": "not_run"}) == {
        "status": "success",
        "failed_messages": 0,
    }


def test_load_json_output_reads_json_before_trailing_log_lines(tmp_path):
    path = tmp_path / "command-output.txt"
    payload = {
        "status": "success",
        "failed_messages": 0,
        "summary": {"records": 3},
    }
    path.write_text(
        "Starting workflow command.\n"
        + json.dumps(payload, indent=2)
        + "\nCleanup finished after summary write.\n",
        encoding="utf-8",
    )

    assert load_json_output(path, {"status": "not_run"}) == payload


def test_load_json_output_can_require_payload_keys(tmp_path):
    path = tmp_path / "command-output.txt"
    payload = {"status": "success", "records": 3}
    diagnostic = {"diagnostic": "cleanup", "records": 99}
    path.write_text(
        json.dumps(payload) + "\n" + json.dumps(diagnostic),
        encoding="utf-8",
    )

    assert load_json_output(path, required_keys=("status",)) == payload


def test_load_json_output_ignores_braces_in_prior_log_lines(tmp_path):
    path = tmp_path / "command-output.txt"
    path.write_text(
        "Diagnostic context: {'operation': 'read'}\n"
        + json.dumps({"status": "partial_failure", "failed_messages": 1}),
        encoding="utf-8",
    )

    assert load_json_output(path) == {
        "status": "partial_failure",
        "failed_messages": 1,
    }


def test_load_json_output_returns_default_for_missing_empty_or_invalid_output(tmp_path):
    fallback = {"status": "not_run"}
    missing = tmp_path / "missing.txt"
    empty = tmp_path / "empty.txt"
    invalid = tmp_path / "invalid.txt"
    empty.write_text("", encoding="utf-8")
    invalid.write_text("Traceback: command failed before JSON output", encoding="utf-8")

    assert load_json_output(missing, fallback) == fallback
    assert load_json_output(empty, fallback) == fallback
    assert load_json_output(invalid, fallback) == fallback
