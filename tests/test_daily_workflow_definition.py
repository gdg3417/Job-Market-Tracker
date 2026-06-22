from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-run.yml"


def test_daily_workflow_yaml_is_valid():
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert isinstance(parsed, dict)
    assert "jobs" in parsed


def test_partial_gmail_failure_withholds_completion_without_failing_step():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "id: gmail_ingestion" in workflow
    assert "failed_messages={failed}" in workflow
    assert "steps.gmail_ingestion.outputs.failed_messages == '0'" in workflow
    assert "the daily completion record will be withheld" in workflow


def test_daily_gate_message_uses_630_central_boundary():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "before 06:30 AM Central" in workflow
