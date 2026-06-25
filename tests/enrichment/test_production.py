from __future__ import annotations

from dataclasses import dataclass

from src.enrichment.production import (
    ProductionLimits,
    recover_stale_in_progress,
    run_production_cycle,
)
from src.models import JobPosting


class RecoverySheet:
    def __init__(self, queue_rows, jobs):
        self.queue_rows = list(queue_rows)
        self.jobs = list(jobs)
        self.queue_updates = []
        self.job_updates = []

    def read_records_with_row_numbers(self, worksheet):
        assert worksheet == "Enrichment_Queue"
        return list(self.queue_rows)

    def read_jobs_with_row_numbers(self):
        return list(self.jobs)

    def update_record(self, worksheet, row_number, record):
        assert worksheet == "Enrichment_Queue"
        self.queue_updates.append((row_number, dict(record)))

    def update_job(self, row_number, job):
        self.job_updates.append((row_number, job))


def _queue(job_key, stage, updated_at):
    return {
        "enrichment_id": f"enrich-{job_key}",
        "job_key": job_key,
        "company": "Example",
        "title": "Director, Strategy",
        "location": "Dallas, TX",
        "lead_url": f"https://example.com/jobs/{job_key}",
        "priority": "high",
        "status": "in_progress",
        "current_stage": stage,
        "attempt_count": 2,
        "last_attempted_at": updated_at,
        "updated_at": updated_at,
    }


def test_stale_in_progress_recovery_returns_each_stage_to_its_safe_handoff():
    sheet = RecoverySheet(
        [
            (2, _queue("direct", "direct_url", "2026-06-25T10:00:00Z")),
            (3, _queue("company", "company_ats", "2026-06-25T10:00:00Z")),
            (4, _queue("external", "external_search", "2026-06-25T10:00:00Z")),
            (5, _queue("fresh", "direct_url", "2026-06-25T11:45:00Z")),
        ],
        [
            (2, JobPosting(job_key="direct", enrichment_status="in_progress")),
            (3, JobPosting(job_key="company", enrichment_status="in_progress")),
            (4, JobPosting(job_key="external", enrichment_status="in_progress")),
            (5, JobPosting(job_key="fresh", enrichment_status="in_progress")),
        ],
    )

    result = recover_stale_in_progress(
        sheet,
        now="2026-06-25T12:00:00Z",
        stale_after_minutes=90,
    )

    assert result.stale_in_progress_found == 3
    assert result.queue_rows_recovered == 3
    assert result.jobs_recovered == 3
    by_row = {row: record for row, record in sheet.queue_updates}
    assert by_row[2]["current_stage"] == "direct_url"
    assert by_row[2]["status"] == "retryable_failure"
    assert by_row[2]["next_attempt_at"] == "2026-06-25T12:00:00Z"
    assert by_row[3]["current_stage"] == "direct_url"
    assert by_row[3]["status"] == "not_found"
    assert by_row[4]["current_stage"] == "company_ats"
    assert by_row[4]["status"] == "not_found"
    assert 5 not in by_row
    assert all(record["error_type"] == "interrupted_run" for record in by_row.values())


@dataclass
class Summary:
    values: dict

    def to_dict(self):
        return dict(self.values)


class ProductionSheet:
    def __init__(self):
        self.runs = []

    def read_records_with_row_numbers(self, worksheet):
        assert worksheet == "Enrichment_Queue"
        return []

    def read_jobs_with_row_numbers(self):
        return []

    def append_run(self, record):
        self.runs.append(record)


def test_daily_production_cycle_orders_pipeline_rescore_and_dashboard(monkeypatch):
    calls = []
    sheet = ProductionSheet()

    monkeypatch.setattr(
        "src.enrichment.production.recover_stale_in_progress",
        lambda *_args, **_kwargs: Summary({"queue_rows_recovered": 0}),
    )

    def pipeline(*_args, **kwargs):
        calls.append(("pipeline", kwargs["direct_limit"], kwargs["company_limit"], kwargs["external_limit"]))
        return {
            "direct_link": {"jobs_evaluated": 4, "direct_attempts": 2, "jobs_updated": 1},
            "company_ats": {"company_ats_attempts": 1, "jobs_updated": 1},
            "external_search": {"search_attempts": 0, "jobs_updated": 0},
        }

    monkeypatch.setattr("src.enrichment.production.run_enrichment_pipeline", pipeline)
    monkeypatch.setattr(
        "src.enrichment.production.rescore_jobs",
        lambda *_args, **_kwargs: calls.append(("rescore",)) or Summary({"jobs_read": 4, "jobs_updated": 2}),
    )
    monkeypatch.setattr(
        "src.enrichment.production.apply_dashboard_and_digest",
        lambda *_args, **_kwargs: calls.append(("dashboard",)) or Summary(
            {"dashboard_rows_written": 10, "digest_rows_written": 8}
        ),
    )
    monkeypatch.setattr(
        "src.enrichment.production.lifecycle_health_metrics",
        lambda *_args, **_kwargs: {"enrichment_backlog": 3},
    )
    monkeypatch.setattr(
        "src.enrichment.production.write_enrichment_health_section",
        lambda *_args, **_kwargs: 11,
    )

    result = run_production_cycle(
        sheet,
        {},
        mode="daily",
        limits=ProductionLimits.for_mode("daily"),
        now="2026-06-25T12:00:00Z",
    )

    assert calls == [
        ("pipeline", 10, 10, 0),
        ("rescore",),
        ("dashboard",),
    ]
    assert result.lifecycle["jobs_checked"] == 0
    assert result.health_metrics["enrichment_backlog"] == 3
    assert result.health_rows_written == 11
    assert len(sheet.runs) == 1
    assert sheet.runs[0]["run_type"] == "sprint_32_enrichment_daily"


def test_weekly_mode_runs_lifecycle_with_controlled_limits(monkeypatch):
    calls = []
    sheet = ProductionSheet()

    monkeypatch.setattr(
        "src.enrichment.production.recover_stale_in_progress",
        lambda *_args, **_kwargs: Summary({"queue_rows_recovered": 0}),
    )
    monkeypatch.setattr(
        "src.enrichment.production.run_enrichment_pipeline",
        lambda *_args, **kwargs: calls.append(
            ("pipeline", kwargs["direct_limit"], kwargs["company_limit"], kwargs["external_limit"])
        )
        or {
            "direct_link": {},
            "company_ats": {},
            "external_search": {"search_attempts": 1},
        },
    )
    monkeypatch.setattr(
        "src.enrichment.production.run_lifecycle_checks",
        lambda *_args, **kwargs: calls.append(("lifecycle", kwargs["limit"]))
        or Summary({"jobs_evaluated": 5, "jobs_checked": 2, "health_metrics": {}}),
    )
    monkeypatch.setattr(
        "src.enrichment.production.rescore_jobs",
        lambda *_args, **_kwargs: Summary({"jobs_read": 5, "jobs_updated": 0}),
    )
    monkeypatch.setattr(
        "src.enrichment.production.apply_dashboard_and_digest",
        lambda *_args, **_kwargs: Summary({"dashboard_rows_written": 5, "digest_rows_written": 5}),
    )
    monkeypatch.setattr(
        "src.enrichment.production.lifecycle_health_metrics",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "src.enrichment.production.write_enrichment_health_section",
        lambda *_args, **_kwargs: 11,
    )

    run_production_cycle(
        sheet,
        {},
        mode="weekly",
        limits=ProductionLimits.for_mode("weekly"),
        now="2026-06-25T12:00:00Z",
    )

    assert calls == [
        ("pipeline", 10, 10, 5),
        ("lifecycle", 50),
    ]


class Worksheet:
    def __init__(self):
        self.calls = []

    def get_all_values(self):
        return [["Existing dashboard"]]

    def update(self, **kwargs):
        self.calls.append(kwargs)


class DashboardSheet:
    def __init__(self):
        self.worksheet = Worksheet()

    def get_worksheet(self, name):
        assert name == "Dashboard"
        return self.worksheet


def test_health_section_is_appended_after_existing_dashboard_rows():
    from src.enrichment.production import write_enrichment_health_section

    sheet = DashboardSheet()
    count = write_enrichment_health_section(
        sheet,
        {
            "enrichment_backlog": 4,
            "retryable_failures": 2,
            "ambiguous_matches": 1,
            "jobs_likely_closed": 3,
            "jobs_confirmed_closed": 5,
            "oldest_pending_enrichment_days": 7,
            "average_enrichment_attempts": 1.5,
            "enrichment_success_rate_percent": 60,
        },
        generated_at="2026-06-25T12:00:00Z",
    )

    assert count == 11
    assert sheet.worksheet.calls[0]["range_name"] == "A3"
    values = sheet.worksheet.calls[0]["values"]
    assert values[0] == ["Enrichment and lifecycle health"]
    assert ["Enrichment backlog", 4, "Open queue items that still require automated work"] in values
