from pathlib import Path

from src.enrichment.production import ProductionRunSummary, _build_run_record


def test_production_dry_run_validates_before_return_without_migrating():
    source = Path("src/enrichment/production.py").read_text(encoding="utf-8")
    dry_run_branch = source.index("if args.dry_run:")
    migrate_call = source.index("migrate_trailing_headers(sheet_client)")

    assert dry_run_branch < migrate_call
    assert source.index("validate_workbook_or_raise(sheet_client)") < dry_run_branch


def test_production_run_record_uses_selected_jobs_not_all_rows_read():
    summary = ProductionRunSummary(
        mode="backfill",
        started_at="2026-06-25T12:00:00Z",
        finished_at="2026-06-25T12:00:01Z",
        direct_link={"jobs_evaluated": 1},
        company_ats={},
        external_search={},
        lifecycle={"jobs_evaluated": 0},
        rescore={"jobs_read": 500, "jobs_selected": 1, "jobs_updated": 1},
    )

    record = _build_run_record(summary)

    assert record["records_found"] == 1
    assert record["rows_read"] == 1


def test_workflow_labels_verified_population_and_health_rows_accurately():
    text = Path(".github/workflows/enrichment-run.yml").read_text(encoding="utf-8")

    assert "Verified jobs after scoring" in text
    assert "Verified scores created" not in text
    assert "Dashboard health rows written" in text
