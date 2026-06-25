from pathlib import Path


def test_production_workflow_has_bounded_modes_and_nonoverlapping_concurrency():
    text = Path(".github/workflows/enrichment-run.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in text
    assert "Job Tracker Daily Run" in text
    assert 'cron: "0 14 * * 0"' in text
    assert "job-tracker-enrichment-production" in text
    assert "cancel-in-progress: false" in text
    assert "python -m src.enrichment.production" in text
    assert "--mode" in text
    assert "timeout-minutes: 45" in text
