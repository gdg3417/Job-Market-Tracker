from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import src.presentation_refresh as refresh
from src.models import JobPosting
from src.surface_status import SurfaceOutcome, merge_surface_outcomes


@dataclass
class DummyResult:
    values: dict

    def to_dict(self) -> dict:
        return dict(self.values)


class FakeClient:
    def __init__(self) -> None:
        self.run_records = []

    def append_run(self, record) -> None:
        self.run_records.append(record)


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "canonical_url": "https://example.com/jobs/123",
        "first_seen_date": "2026-07-01",
        "status": "open",
        "review_status": "not_reviewed",
        "application_status": "",
        "role_level": "Senior Manager",
        "potential_priority": "high",
        "potential_priority_score": 80,
        "total_score": 80,
        "alert_tier": "strong_fit",
        "score_status": "verified",
        "verified_total_score": 80,
        "verified_alert_tier": "strong_fit",
    }
    values.update(overrides)
    return JobPosting(**values)


def install_refresh_stubs(
    monkeypatch,
    writes,
    *,
    fail_follow_up: bool = False,
    fail_weekly_readback: bool = False,
    fail_surface_status: bool = False,
) -> None:
    snapshot = refresh.CanonicalSnapshot(
        jobs_with_rows=[(2, make_job())],
        rejected_rows=[],
        target_company_rows=[],
        config_company_rows=[],
        runs_rows=[],
        weekly_value_rows=[{"Week Start": "2026-07-06", "Week End": "2026-07-12"}],
    )
    monkeypatch.setattr(refresh, "read_canonical_snapshot", lambda client: snapshot)

    def review_action(client):
        writes.append("Review_Queue")
        return DummyResult({"review_queue_rows_written": 2, "warnings": []})

    def follow_action(client, *, as_of=None):
        writes.append("Follow_Up_Queue")
        if fail_follow_up:
            raise RuntimeError("simulated follow-up write failure")
        return DummyResult({"rows_written": 2, "warnings": []})

    def weekly_action(*args, **kwargs):
        writes.append("Weekly_Value")
        return DummyResult({"rows_written": 3, "warnings": []})

    def context_action(*args, **kwargs):
        writes.append("Weekly_Context")
        return DummyResult({"rows_written": 4, "warnings": []})

    def read_optional(client, name):
        if fail_weekly_readback and name == refresh.WEEKLY_VALUE_SHEET:
            raise RuntimeError("simulated weekly value readback failure")
        return [{"Week Start": "2026-07-06", "Week End": "2026-07-12"}]

    monkeypatch.setattr(refresh, "apply_review_queue", review_action)
    monkeypatch.setattr(refresh, "apply_follow_up_queue", follow_action)
    monkeypatch.setattr(refresh, "apply_weekly_value", weekly_action)
    monkeypatch.setattr(refresh, "apply_weekly_context", context_action)
    monkeypatch.setattr(refresh, "load_weekly_digest_config", lambda path=None: None)
    monkeypatch.setattr(refresh, "_read_optional_records", read_optional)
    monkeypatch.setattr(
        refresh.dashboard,
        "build_digest_values",
        lambda *args, **kwargs: [
            ["Job Market Tracker Weekly Digest"],
            ["Generated at", "2026-07-14T12:00:00+00:00"],
            ["Review order", "test"],
            [],
            list(refresh.dashboard.DIGEST_HEADERS),
            ["Immediate review", "Acme Industrial", "Role"],
        ],
    )
    monkeypatch.setattr(
        refresh.dashboard,
        "build_dashboard_values",
        lambda *args, **kwargs: [["Job Market Tracker Dashboard"], ["Last refreshed", "test"]],
    )

    def write_values(client, name, values):
        writes.append(name)

    def status_action(client, outcomes, **kwargs):
        if fail_surface_status:
            raise RuntimeError("simulated surface status write failure")
        return list(outcomes)

    monkeypatch.setattr(refresh.dashboard, "write_values", write_values)
    monkeypatch.setattr(refresh, "write_surface_status", status_action)


def test_unified_refresh_uses_required_deterministic_order(monkeypatch):
    writes = []
    install_refresh_stubs(monkeypatch, writes)
    result = refresh.apply_presentation_refresh(
        FakeClient(),
        as_of="2026-07-14",
        source_run="test",
    )
    assert result["status"] == "success"
    assert result["surface_order"] == [
        "Review_Queue",
        "Follow_Up_Queue",
        "Weekly_Value",
        "Weekly_Context",
        "Dashboard",
        "Digest",
    ]
    assert writes == result["surface_order"]
    assert result["jobs_snapshot_rows"] == 1
    assert result["dashboard_rows_written"] == 2
    assert result["digest_rows_written"] == 6


def test_unified_refresh_is_safe_to_rerun(monkeypatch):
    writes = []
    install_refresh_stubs(monkeypatch, writes)
    client = FakeClient()
    first = refresh.apply_presentation_refresh(client, as_of="2026-07-14", source_run="test")
    second = refresh.apply_presentation_refresh(client, as_of="2026-07-14", source_run="test")
    expected = [
        "Review_Queue",
        "Follow_Up_Queue",
        "Weekly_Value",
        "Weekly_Context",
        "Dashboard",
        "Digest",
    ]
    assert first["status"] == second["status"] == "success"
    assert writes == [*expected, *expected]
    assert len(client.run_records) == 2


def test_partial_failure_is_recorded_and_later_surfaces_continue(monkeypatch):
    writes = []
    install_refresh_stubs(monkeypatch, writes, fail_follow_up=True)
    result = refresh.apply_presentation_refresh(
        FakeClient(),
        as_of="2026-07-14",
        source_run="test",
    )
    assert result["status"] == "partial_failure"
    assert result["surfaces_failed"] == 1
    assert result["results"]["Follow_Up_Queue"]["status"] == "failed"
    assert "Weekly_Value" in writes
    assert "Dashboard" in writes
    assert "Digest" in writes


def test_weekly_value_readback_failure_uses_prior_snapshot_and_continues(monkeypatch):
    writes = []
    install_refresh_stubs(monkeypatch, writes, fail_weekly_readback=True)
    result = refresh.apply_presentation_refresh(
        FakeClient(),
        as_of="2026-07-14",
        source_run="test",
    )
    assert result["status"] == "success"
    assert result["surfaces_failed"] == 0
    assert result["surfaces_with_warnings"] == 2
    assert "readback failed" in result["results"]["Weekly_Value"]["warnings"][0]
    assert "readback failed" in result["results"]["Weekly_Context"]["warnings"][0]
    assert "Dashboard" in writes
    assert "Digest" in writes


def test_surface_status_failure_is_returned_as_a_surface_failure(monkeypatch):
    writes = []
    install_refresh_stubs(monkeypatch, writes, fail_surface_status=True)
    result = refresh.apply_presentation_refresh(
        FakeClient(),
        as_of="2026-07-14",
        source_run="test",
    )
    assert result["status"] == "partial_failure"
    assert result["surfaces_failed"] == 1
    assert result["surface_status_written"] is False
    assert result["surface_order"][-1] == "Surface_Status"
    assert result["surface_status"][-1]["status"] == "failed"
    assert "simulated surface status write failure" in result["surface_status_error"]


def test_surface_status_preserves_last_success_on_failure():
    existing = {
        "Review_Queue": {
            "surface_name": "Review_Queue",
            "last_successful_refresh": "2026-07-13T12:00:00+00:00",
        }
    }
    merged = merge_surface_outcomes(
        existing,
        [
            SurfaceOutcome(
                surface_name="Review_Queue",
                status="failed",
                warning_or_error="simulated",
            )
        ],
        source_run="test",
        data_as_of_date="2026-07-14",
        attempted_at="2026-07-14T12:00:00+00:00",
    )
    assert merged[0].last_successful_refresh == "2026-07-13T12:00:00+00:00"
    assert merged[0].last_attempted_at == "2026-07-14T12:00:00+00:00"


def test_generated_write_workflows_share_one_concurrency_group():
    root = Path(__file__).resolve().parents[1]
    workflow_names = [
        "daily-run.yml",
        "enrichment-run.yml",
        "weekly-value.yml",
        "sheet-governance.yml",
        "verification-health.yml",
        "workbook-capacity.yml",
    ]
    for workflow_name in workflow_names:
        text = (root / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
        assert "group: job-tracker-workbook-writes" in text
        assert "queue: max" in text
