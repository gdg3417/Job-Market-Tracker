from pathlib import Path


def test_production_workflow_has_bounded_modes_and_nonoverlapping_concurrency():
    text = Path(".github/workflows/enrichment-run.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in text
    assert "Job Tracker Daily Run" in text
    assert 'cron: "0 14 * * 0"' in text
    assert "job-tracker-workbook-writes" in text
    assert "cancel-in-progress: false" in text
    assert "python -m src.enrichment.production" in text
    assert "python -m src.presentation_refresh" in text
    assert "--mode" in text
    assert "timeout-minutes: 60" in text


def test_production_workflow_summary_exposes_resolution_metrics():
    text = Path(".github/workflows/enrichment-run.yml").read_text(encoding="utf-8")

    assert 'resolution = data.get("authoritative_resolution") or {}' in text
    assert "Authoritative resolution attempts" in text
    assert "Authoritative resolutions" in text
    assert "Resolution manual interventions" in text
