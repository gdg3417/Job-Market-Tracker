import sys
from pathlib import Path

import pytest

from src.enrichment.pipeline import run_enrichment_pipeline
from src.enrichment.production import ProductionRunSummary, _build_run_record, parse_args


class Summary:
    def __init__(self, **values):
        self.values = values

    def to_dict(self):
        return dict(self.values)


def test_production_dry_run_validates_and_returns_before_migration():
    source = Path("src/enrichment/production.py").read_text(encoding="utf-8")
    dry_run_branch = source.index("if args.dry_run:")
    validate_call = source.index("validate_workbook_or_raise(sheet_client)", dry_run_branch)
    dry_run_return = source.index("return", validate_call)
    migrate_call = source.index("migrate_trailing_headers(sheet_client)")

    assert dry_run_branch < validate_call < dry_run_return < migrate_call


def test_production_execution_modes_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["production", "--run", "--dry-run"])

    with pytest.raises(SystemExit):
        parse_args()


def test_production_run_record_separates_selected_jobs_from_rows_read():
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
    assert record["rows_read"] == 500


def test_pipeline_skips_external_stage_when_limit_is_zero(monkeypatch):
    monkeypatch.setattr(
        "src.enrichment.pipeline.run_direct_link_enrichment",
        lambda *_args, **_kwargs: Summary(jobs_evaluated=0),
    )
    monkeypatch.setattr(
        "src.enrichment.pipeline.run_company_ats_enrichment",
        lambda *_args, **_kwargs: Summary(jobs_evaluated=0),
    )

    def unexpected_external_call(*_args, **_kwargs):
        raise AssertionError("external search should not run when external_limit is zero")

    monkeypatch.setattr(
        "src.enrichment.pipeline.run_external_search_enrichment",
        unexpected_external_call,
    )

    result = run_enrichment_pipeline(object(), external_limit=0)

    assert result["external_search"]["jobs_evaluated"] == 0
    assert result["external_search"]["queries_executed"] == 0


def test_workflow_metrics_are_accurate_and_schema_work_is_not_duplicated():
    text = Path(".github/workflows/enrichment-run.yml").read_text(encoding="utf-8")

    assert "Verified jobs after scoring" in text
    assert "Verified scores created" not in text
    assert "Dashboard health rows written" in text
    assert "External queries executed" in text
    assert "python -m src.schema --migrate" not in text
