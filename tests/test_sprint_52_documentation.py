from __future__ import annotations

from pathlib import Path

import yaml

from src.gmail_diagnostics import GMAIL_FAILURES_WORKSHEET
from src.schema import CANONICAL_SCHEMA
from src.sheet_governance_policy import SHEET_GUIDE, SHEET_POLICIES
from src.surface_status import SURFACE_STATUS_SHEET


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKBOOK_MAP = (ROOT / "docs" / "WORKBOOK_MAP.md").read_text(encoding="utf-8")
WORKFLOW_MAP = (ROOT / "docs" / "WORKFLOW_OWNERSHIP.md").read_text(encoding="utf-8")
TROUBLESHOOTING = (ROOT / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
OPERATIONS = (ROOT / "docs" / "operations_runbook.md").read_text(encoding="utf-8")
SPRINT_52 = (ROOT / "docs" / "sprint_52_documentation_readiness.md").read_text(encoding="utf-8")

EXPECTED_WORKFLOWS = {
    "daily-run.yml": {
        "name": "Job Tracker Daily Run",
        "jobs": {"daily-run"},
        "crons": {"30 11 * * *", "30 12 * * *"},
    },
    "enrichment-run.yml": {
        "name": "Job Tracker Enrichment Run",
        "jobs": {"enrichment"},
        "crons": {"0 14 * * 0"},
    },
    "pull-request-tests.yml": {
        "name": "Pull Request Tests",
        "jobs": {"test"},
        "crons": set(),
    },
    "regression-readiness.yml": {
        "name": "Regression readiness",
        "jobs": {"regression-readiness"},
        "crons": set(),
    },
    "sheet-governance.yml": {
        "name": "Job Tracker Sheet UX Governance",
        "jobs": {"sheet-governance"},
        "crons": {"0 15 * * *"},
    },
    "source-quality.yml": {
        "name": "Job Tracker Source Quality",
        "jobs": {"source-quality"},
        "crons": {"30 13 * * 1"},
    },
    "verification-health.yml": {
        "name": "Job Tracker Verification Health",
        "jobs": {"verification-health"},
        "crons": set(),
    },
    "weekly-value.yml": {
        "name": "Job Tracker Weekly Value Refresh",
        "jobs": {"weekly-value-refresh"},
        "crons": {"0 12 * * 1", "15 14 * * *"},
    },
    "workbook-capacity.yml": {
        "name": "Job Tracker Workbook Capacity",
        "jobs": {"workbook-capacity"},
        "crons": {"15 14 1 * *"},
    },
}


# These worksheets are created outside CANONICAL_SCHEMA and are not all governed
# through SHEET_POLICIES. Import their names from production modules so renames
# cannot silently leave the documentation contract stale.
EXTRA_OPERATIONAL_SHEETS = {
    GMAIL_FAILURES_WORKSHEET,
    SURFACE_STATUS_SHEET,
    SHEET_GUIDE,
}


def _workflow_text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_all_current_workflow_yaml_is_valid_and_documented():
    current = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}

    assert current == set(EXPECTED_WORKFLOWS)
    for workflow_name, expected in sorted(EXPECTED_WORKFLOWS.items()):
        text = _workflow_text(workflow_name)
        assert yaml.compose(text) is not None

        parsed = yaml.safe_load(text)
        assert parsed["name"] == expected["name"]
        assert set(parsed["jobs"]) == expected["jobs"]

        assert f"`.github/workflows/{workflow_name}`" in WORKFLOW_MAP
        assert f"`{expected['name']}`" in WORKFLOW_MAP
        for job_context in expected["jobs"]:
            assert f"`{job_context}`" in WORKFLOW_MAP
        for cron in expected["crons"]:
            assert f'cron: "{cron}"' in text
            assert f"`{cron}`" in WORKFLOW_MAP


def test_required_check_names_and_job_contexts_are_documented_exactly():
    expected = {
        "Pull Request Tests": "test",
        "Regression readiness": "regression-readiness",
    }
    for workflow_name, job_context in expected.items():
        assert f"`{workflow_name}`" in README
        assert f"`{workflow_name}`" in WORKFLOW_MAP
        assert f"`{workflow_name}`" in SPRINT_52
        assert f"`{job_context}`" in WORKFLOW_MAP
        assert f"`{job_context}`" in SPRINT_52

    assert "workflow display name" in WORKFLOW_MAP.lower()
    assert "job-level check context" in WORKFLOW_MAP.lower()
    assert "data/regression/sprint38_gold_standard_jobs.json" in README
    assert "data/regression/sprint38_gold_standard_jobs.json" in WORKFLOW_MAP
    assert "data/regression/sprint38_gold_standard_jobs.json" in SPRINT_52


def test_all_current_workbook_surfaces_are_documented():
    expected = set(CANONICAL_SCHEMA) | set(SHEET_POLICIES) | EXTRA_OPERATIONAL_SHEETS

    for worksheet_name in sorted(expected):
        assert f"`{worksheet_name}`" in WORKBOOK_MAP

    assert "`Jobs` is the canonical source of truth" in WORKBOOK_MAP
    assert "Generated surfaces are read-only" in WORKBOOK_MAP
    assert "Manual authoritative URL distinction" in WORKBOOK_MAP


def test_maintenance_cadence_and_post_merge_validation_are_documented():
    for heading in (
        "## Daily operating cycle",
        "## Weekly operating cycle",
        "## Monthly operating cycle",
        "## Quarterly operating cycle",
    ):
        assert heading in OPERATIONS

    for phrase in (
        "Daily ingestion cycle",
        "Production enrichment cycle",
        "Verification-health cycle",
        "Unified generated-surface refresh",
        "Workbook-capacity guard",
        "Source-quality state",
        "Is another feature sprint justified?",
    ):
        assert phrase in SPRINT_52


def test_required_recovery_topics_are_documented():
    recovery_topics = (
        "Gmail backlog or failed messages",
        "Gmail credentials or authentication",
        "Google Sheets quota exhaustion",
        "Verification-health failure",
        "Stale or partially refreshed generated surfaces",
        "Workbook-capacity warning or critical result",
        "Static source failure",
        "Schema mismatch or edited headers",
        "Enrichment failure or stuck queue work",
        "Authoritative posting resolution problem",
        "Weekly Context email failure",
        "Duplicate or replay concern",
    )

    for topic in recovery_topics:
        assert f"## {topic}" in TROUBLESHOOTING


def test_readme_reports_maintenance_mode_through_sprint_52():
    assert "Sprints 1 through 52" in README
    assert "The tracker is in maintenance mode" in README
    assert "docs/WORKBOOK_MAP.md" in README
    assert "docs/WORKFLOW_OWNERSHIP.md" in README
    assert "docs/sprint_52_documentation_readiness.md" in README
