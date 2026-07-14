from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/source-quality.yml")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_source_quality_workflow_exists_and_runs_weekly():
    text = _text()

    assert "name: Job Tracker Source Quality" in text
    assert 'cron: "30 13 * * 1"' in text
    assert "workflow_dispatch:" in text
    assert "group: job-tracker-workbook-writes" in text
    assert "cancel-in-progress: false" in text


def test_cleanup_requires_exact_explicit_company_ids():
    text = _text()

    assert "apply_reviewed_cleanup" in text
    assert "approved_company_ids" in text
    assert "apply_reviewed_cleanup requires at least one exact company_id" in text
    assert "--approved-company-id" in text
    assert "No valid company_id values were supplied" in text


def test_workflow_runs_full_validation_before_workbook_write():
    text = _text()

    assert "run: pytest" in text
    assert "python -m src.schema --migrate" in text
    assert text.index("run: pytest") < text.index("python -m src.source_quality_report")
    assert text.index("python -m src.schema --migrate") < text.index("python -m src.source_quality_report")


def test_workflow_writes_complete_report_and_applies_governance():
    text = _text()

    assert "python -m src.source_quality_report" in text
    assert "--write-report" in text
    assert "python -m src.sheet_governance --apply" in text
    assert text.index("python -m src.source_quality_report") < text.index("python -m src.sheet_governance --apply")
    assert "Zero-result configuration rows" in text
    assert "Configured searches with unavailable attribution" in text
    assert '"attribution_unavailable_rows": 0' in text


def test_step_summary_preserves_source_change_audit_trail():
    text = _text()

    assert "original_source_url" in text
    assert "final_source_url" in text
    assert " -> " in text


def test_scheduled_report_mode_cannot_apply_unapproved_cleanup():
    text = _text()

    assert "MODE: ${{ inputs.mode || 'report' }}" in text
    assert "default: report" in text
    assert 'if [ "${MODE}" = "apply_reviewed_cleanup" ]; then' in text
