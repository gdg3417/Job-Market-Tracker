from __future__ import annotations

from pathlib import Path

from src.schema import CANONICAL_SCHEMA
from src.sheet_governance_policy import SHEET_POLICIES


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKBOOK_MAP = (ROOT / "docs" / "WORKBOOK_MAP.md").read_text(encoding="utf-8")
WORKFLOW_MAP = (ROOT / "docs" / "WORKFLOW_OWNERSHIP.md").read_text(encoding="utf-8")
TROUBLESHOOTING = (ROOT / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
OPERATIONS = (ROOT / "docs" / "operations_runbook.md").read_text(encoding="utf-8")
SPRINT_52 = (ROOT / "docs" / "sprint_52_documentation_readiness.md").read_text(encoding="utf-8")

EXPECTED_WORKFLOWS = {
    "daily-run.yml",
    "enrichment-run.yml",
    "pull-request-tests.yml",
    "regression-readiness.yml",
    "sheet-governance.yml",
    "source-quality.yml",
    "verification-health.yml",
    "weekly-value.yml",
    "workbook-capacity.yml",
}

EXTRA_OPERATIONAL_SHEETS = {
    "Gmail_Failures",
    "Review_Queue",
    "Follow_Up_Queue",
    "Weekly_Value",
    "Weekly_Context",
    "Surface_Status",
    "Source_Audit",
    "Source_Yield",
    "Sheet_Guide",
}


def test_all_current_workflows_are_documented():
    current = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}

    assert current == EXPECTED_WORKFLOWS
    for workflow_name in sorted(current):
        assert f"`.github/workflows/{workflow_name}`" in WORKFLOW_MAP


def test_required_check_names_are_documented_exactly():
    for check_name in ("Pull Request Tests", "Regression readiness"):
        assert f"`{check_name}`" in README
        assert f"`{check_name}`" in WORKFLOW_MAP
        assert f"`{check_name}`" in SPRINT_52

    assert "data/regression/sprint38_gold_standard_jobs.json" in README
    assert "data/regression/sprint38_gold_standard_jobs.json" in WORKFLOW_MAP
    assert "data/regression/sprint38_gold_standard_jobs.json" in SPRINT_52


def test_all_current_workbook_surfaces_are_documented():
    expected = set(CANONICAL_SCHEMA) | set(SHEET_POLICIES) | EXTRA_OPERATIONAL_SHEETS

    for worksheet_name in sorted(expected):
        assert f"`{worksheet_name}`" in WORKBOOK_MAP

    assert "`Jobs` is the canonical source of truth" in WORKBOOK_MAP
    assert "Generated surfaces are read-only" in WORKBOOK_MAP


def test_maintenance_cadence_and_post_merge_validation_are_documented():
    for heading in ("## Daily operating cycle", "## Weekly operating cycle", "## Monthly operating cycle", "## Quarterly operating cycle"):
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
