import sys

from src.verification_health import (
    build_dashboard_section,
    build_run_record,
    calculate_from_workbook,
    calculate_verification_health,
    parse_args,
    prepare_workbook_schema,
    upsert_run_record,
)
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def test_dashboard_rendering_contains_required_sections():
    result = calculate_verification_health(
        jobs=[job("one", first_seen_date="2026-06-20", created_at="2026-06-20T12:00:00Z")],
        job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()],
        target_company_rows=[{"company_name": "Acme Industrial", "priority_tier": "Tier 1"}],
        as_of=AS_OF,
    )
    flattened = "\n".join(str(cell) for row in build_dashboard_section(result) for cell in row)
    for expected in [
        "Verification funnel", "Verification aging", "Service-level breaches",
        "Top blocker reasons", "Health component scores",
        "Oldest unresolved high-potential jobs",
        "Oldest unresolved target-company jobs",
        "Jobs requiring manual intervention",
    ]:
        assert expected in flattened


def test_historical_run_write_is_idempotent():
    class FakeClient:
        def __init__(self):
            self.rows = []
            self.updated = []
            self.appended = []

        def read_records_with_row_numbers(self, worksheet_name):
            assert worksheet_name == "Runs"
            return [(index + 2, row) for index, row in enumerate(self.rows)]

        def update_record(self, worksheet_name, row_number, record):
            assert worksheet_name == "Runs"
            self.rows[row_number - 2] = record
            self.updated.append(row_number)

        def append_run(self, record):
            self.rows.append(record)
            self.appended.append(record["run_id"])

    result = calculate_verification_health(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    record = build_run_record(result)
    client = FakeClient()
    assert upsert_run_record(client, record) == "inserted"
    assert upsert_run_record(client, record) == "updated"
    assert len(client.rows) == 1
    assert client.appended == [record["run_id"]]
    assert client.updated == [2]


def test_calculate_from_workbook_uses_only_existing_canonical_tabs():
    class FakeClient:
        def __init__(self):
            self.read_names = []

        def read_records(self, name):
            self.read_names.append(name)
            return [successful_daily_run()] if name == "Runs" else []

    client = FakeClient()
    result = calculate_from_workbook(client, as_of=AS_OF)
    assert result.records_read["jobs"] == 0
    assert set(client.read_names) == {
        "Jobs", "Job_Sources", "Enrichment_Queue", "Enrichment_Evidence",
        "Runs", "Posting_Resolution", "Target_Companies", "Config_Companies",
    }


def test_prevalidated_schema_skips_duplicate_workbook_preflight():
    class NoWorkbookAccessExpected:
        def __getattr__(self, name):
            raise AssertionError(f"Unexpected workbook access through {name}")

    action = prepare_workbook_schema(
        NoWorkbookAccessExpected(),
        dry_run=False,
        schema_prevalidated=True,
    )
    assert action == "prevalidated"


def test_cli_accepts_schema_prevalidated_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verification_health", "--run", "--schema-prevalidated"])
    args = parse_args()
    assert args.run is True
    assert args.schema_prevalidated is True


def test_run_record_uses_existing_runs_schema_shape():
    result = calculate_verification_health(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    record = build_run_record(result)
    assert set(record) == {
        "run_id", "run_type", "source_type", "source_name", "status",
        "started_at", "finished_at", "duration_seconds", "records_found",
        "records_inserted", "records_updated", "records_failed", "rows_read",
        "config_companies_rows", "config_searches_rows", "companies_read",
        "searches_read", "error_message", "notes", "created_at", "updated_at",
    }
