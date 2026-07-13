from pathlib import Path


WORKFLOW = Path(".github/workflows/workbook-capacity.yml")


def test_capacity_workflow_exists_and_runs_scheduled_audits():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "python -m src.workbook_capacity --audit --enforce-critical" in text


def test_compaction_requires_explicit_manual_apply_and_formatting_approval_inputs():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "apply_compaction:" in text
    assert "allow_trim_blank_formatting:" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.apply_compaction" in text
    assert "--allow-trim-blank-formatting" in text
    assert "python -m src.workbook_capacity --compact --apply" in text
    assert "github.event_name != 'workflow_dispatch' || !inputs.apply_compaction" in text


def test_capacity_workflow_runs_focused_tests_and_schema_validation():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pytest tests/test_workbook_capacity.py tests/test_workbook_capacity_workflow.py" in text
    assert "python -m src.schema --validate" in text
    assert "actions/upload-artifact@v4" in text


def test_scheduled_workflow_cannot_apply_compaction():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.apply_compaction }}" in text
    assert "cancel-in-progress: false" in text
