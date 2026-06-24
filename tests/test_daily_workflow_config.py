from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-run.yml"


def _daily_run_steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["daily-run"]["steps"]


def test_daily_workflow_yaml_is_valid_and_has_expected_steps():
    step_names = [str(step.get("name") or "") for step in _daily_run_steps()]

    assert "Migrate and validate workbook schema" in step_names
    assert "Record workflow validation" in step_names
    assert "Run Gmail ingestion" in step_names
    assert "Write GitHub Step Summary" in step_names


def test_daily_workflow_does_not_run_redundant_gmail_ledger_preflight():
    step_names = [str(step.get("name") or "") for step in _daily_run_steps()]

    assert "Ensure Gmail message ledger" not in step_names
    assert step_names.index("Migrate and validate workbook schema") < step_names.index("Run Gmail ingestion")


def test_daily_workflow_uses_resilient_json_output_parser():
    steps = {str(step.get("name") or ""): step for step in _daily_run_steps()}

    assert "load_json_output" in steps["Run Gmail ingestion"]["run"]
    assert "load_json_output" in steps["Write GitHub Step Summary"]["run"]
