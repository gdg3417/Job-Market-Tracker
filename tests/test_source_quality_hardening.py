from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.source_quality import (
    HEALTHY,
    MANUAL_REVIEW,
    PERMANENT_404,
    STRUCTURED_ATS,
    SourceAuditFinding,
    apply_approved_source_updates,
    audit_static_sources,
    build_source_yield_report,
    detect_structured_ats,
    filter_static_sources_for_execution,
    prior_failure_observations,
    probe_source,
    run_source_quality,
)
from src.source_quality_inventory import configured_static_source_rows_for_audit
from src.source_quality_report import configured_zero_yield_rows, run_source_quality_report


class FakeResponse:
    def __init__(self, status_code=200, text="", url="https://example.com/careers", history=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.history = list(history or [])
        self.headers = {}


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, *, timeout, headers, allow_redirects):
        return self.response


class FakeUpdateClient:
    def __init__(self):
        self.updates = []

    def update_record(self, worksheet_name, row_number, record):
        self.updates.append((worksheet_name, row_number, dict(record)))


def _company(company_id="example", source_url="https://example.com/jobs", **overrides):
    row = {
        "company_id": company_id,
        "company_name": "Example Co",
        "source_type": "static_page",
        "source_url": source_url,
        "ingestion_mode": "static_direct",
        "active": "TRUE",
    }
    row.update(overrides)
    return row


def _finding(company_id="example", source_url="https://example.com/jobs", **overrides):
    values = {
        "company_id": company_id,
        "company_name": "Example Co",
        "source_url": source_url,
        "final_url": source_url,
        "source_type": "static_page",
        "ats_platform": "",
        "classification": PERMANENT_404,
        "http_status": 404,
        "retry_eligible": False,
        "retry_after": "",
        "requires_configuration_change": True,
        "failure_observations": 2,
        "recommended_action": "replace_or_retire_source",
        "recommendation_reason": "Repeated 404",
        "observed_at": "2026-07-14T12:00:00Z",
    }
    values.update(overrides)
    return SourceAuditFinding(**values)


def test_plain_language_leverage_does_not_trigger_lever_ats():
    assert detect_structured_ats(
        "https://example.com/careers",
        "We leverage technology to improve customer outcomes.",
    ) == ""


def test_platform_domain_and_exact_metadata_still_detect_ats():
    assert detect_structured_ats("https://jobs.lever.co/example") == "lever"
    assert detect_structured_ats("https://example.com/careers", "lever") == "lever"


def test_redirect_without_job_signal_requires_manual_review():
    response = FakeResponse(
        status_code=200,
        text="Welcome to our corporate homepage",
        url="https://example.com/",
        history=[FakeResponse(status_code=301)],
    )
    result = probe_source(
        "https://example.com/old-careers",
        session=FakeSession(response),
    )
    assert result.classification == MANUAL_REVIEW
    assert result.error_category == "redirect_without_job_signal"


def test_successful_recovery_resets_prior_failure_streak():
    runs = [
        {
            "source_type": "static_page",
            "source_name": "static_page:Example Co",
            "status": "failed",
            "finished_at": "2026-07-01T12:00:00Z",
            "notes": "url=https://example.com/jobs; http_status=404",
        },
        {
            "source_type": "static_page",
            "source_name": "static_page:Example Co",
            "status": "success",
            "finished_at": "2026-07-05T12:00:00Z",
            "notes": "url=https://example.com/jobs",
        },
        {
            "source_type": "static_page",
            "source_name": "static_page:Example Co",
            "status": "failed",
            "finished_at": "2026-07-10T12:00:00Z",
            "notes": "url=https://example.com/jobs; http_status=404",
        },
    ]
    count = prior_failure_observations(
        runs,
        company_name="Example Co",
        source_url="https://example.com/jobs",
        classification=PERMANENT_404,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert count == 1


def test_runtime_gate_skips_active_cooldown_and_configuration_changes():
    companies = [_company()]
    cooldown_audit = [
        {
            "company_id": "example",
            "source_url": "https://example.com/jobs",
            "classification": "temporarily_blocked",
            "retry_eligible": "TRUE",
            "retry_after": "2026-07-20T00:00:00Z",
            "requires_configuration_change": "FALSE",
            "observed_at": "2026-07-14T12:00:00Z",
        }
    ]
    execute, skipped = filter_static_sources_for_execution(
        companies,
        cooldown_audit,
        as_of=datetime(2026, 7, 15, tzinfo=UTC),
    )
    assert execute == []
    assert skipped[0]["reason"] == "cooldown_active"

    execute, skipped = filter_static_sources_for_execution(
        companies,
        cooldown_audit,
        as_of=datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert len(execute) == 1
    assert skipped == []

    change_required = [{**cooldown_audit[0], "requires_configuration_change": "TRUE", "retry_after": ""}]
    execute, skipped = filter_static_sources_for_execution(
        companies,
        change_required,
        as_of=datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert execute == []
    assert skipped[0]["reason"] == "configuration_change_required"


def test_approved_update_matches_exact_company_and_source_url():
    rows = [
        (2, _company(source_url="https://example.com/one")),
        (3, _company(source_url="https://example.com/two")),
    ]
    finding = _finding(source_url="https://example.com/two")
    client = FakeUpdateClient()

    updates = apply_approved_source_updates(
        rows,
        [finding],
        approved_company_ids={"example"},
        sheet_client=client,
    )

    assert len(updates) == 1
    assert client.updates[0][1] == 3
    assert updates[0]["original_source_url"] == "https://example.com/two"
    assert updates[0]["before"]["active"] == "TRUE"
    assert updates[0]["after"]["active"] == "FALSE"


def test_review_yield_is_bounded_for_application_status_only():
    rows = build_source_yield_report(
        jobs=[
            {
                "job_key": "job1",
                "company": "Example Co",
                "source_primary": "static_page",
                "status": "open",
                "review_status": "not_reviewed",
                "application_status": "applied",
                "potential_priority": "low",
            }
        ],
        job_sources=[
            {
                "source_key": "s1",
                "job_key": "job1",
                "company": "Example Co",
                "source_primary": "static_page",
                "source_type": "static_page",
                "source_url": "https://example.com/jobs",
                "first_seen_date": "2026-07-10",
            }
        ],
        rejected_jobs=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )
    row = next(item for item in rows if item.group_type == "static_company_source")
    assert row.surfaced_for_review == 1
    assert row.applied == 1
    assert row.review_yield_percent == 100.0


def test_audit_includes_failed_static_sources_without_reenabling_execution():
    companies = [
        _company(company_id="custom", source_type="custom", source_url="https://example.com/careers"),
        _company(company_id="blocked", source_url="https://example.com/careers", source_quality="failed"),
        _company(company_id="gmail", ingestion_mode="gmail_only"),
    ]
    audit_rows = configured_static_source_rows_for_audit(companies)
    findings = audit_static_sources(
        audit_rows,
        session=FakeSession(FakeResponse(text="Current openings")),
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert [finding.company_id for finding in findings] == ["custom", "blocked"]
    assert all(finding.classification == HEALTHY for finding in findings)

    execute, _ = filter_static_sources_for_execution(companies, [])
    assert [row["company_id"] for row in execute] == ["custom"]


def test_configured_search_is_marked_attribution_unavailable_not_zero_yield():
    rows = configured_zero_yield_rows(
        company_rows=[],
        search_rows=[{"search_id": "dallas_strategy_manager", "active": "TRUE"}],
        target_companies=[],
        existing_rows=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )
    assert len(rows) == 1
    assert rows[0].recommendation == "attribution_unavailable"
    assert "Do not interpret" in rows[0].recommendation_reason


def test_report_persists_evidence_before_approved_configuration_update(monkeypatch):
    events = []

    class FakeClient:
        def read_records_with_row_numbers(self, worksheet_name):
            assert worksheet_name == "Config_Companies"
            return [(2, _company())]

        def read_records(self, worksheet_name):
            return []

        def append_run(self, record):
            events.append("append_run")

    finding = _finding()
    monkeypatch.setattr("src.source_quality_report.audit_static_sources", lambda *args, **kwargs: [finding])
    monkeypatch.setattr("src.source_quality_report.build_source_yield_report", lambda **kwargs: [])
    monkeypatch.setattr("src.source_quality_report.configured_zero_yield_rows", lambda **kwargs: [])
    monkeypatch.setattr(
        "src.source_quality_report.write_source_quality_surfaces",
        lambda *args, **kwargs: events.append("write_surfaces") or {
            "source_audit_rows_written": 1,
            "source_yield_rows_written": 0,
        },
    )
    monkeypatch.setattr(
        "src.source_quality_report.apply_approved_source_updates",
        lambda *args, **kwargs: events.append("update_config") or [],
    )

    run_source_quality_report(
        write_report=True,
        approved_company_ids={"example"},
        sheet_client=FakeClient(),
    )

    assert events[:2] == ["write_surfaces", "update_config"]
    assert events[-1] == "append_run"


def test_daily_static_run_reads_and_enforces_source_audit():
    text = Path("src/main.py").read_text(encoding="utf-8")
    assert '_read_optional_records(sheet_client, "Source_Audit")' in text
    assert "filter_static_sources_for_execution(company_rows, audit_rows)" in text
    assert 'status = "all_sources_in_cooldown"' in text

def test_structured_ats_conversion_populates_required_source_slug():
    cases = [
        ("greenhouse", "https://boards.greenhouse.io/acme/jobs", "acme"),
        ("lever", "https://jobs.lever.co/acme", "acme"),
    ]
    for platform, final_url, expected_slug in cases:
        row = _company(source_url="https://example.com/careers", source_slug="")
        finding = _finding(
            source_url=row["source_url"],
            final_url=final_url,
            classification=STRUCTURED_ATS,
            ats_platform=platform,
            http_status=200,
            retry_eligible=False,
            requires_configuration_change=True,
            failure_observations=0,
            recommended_action="prefer_structured_ats",
        )
        client = FakeUpdateClient()

        updates = apply_approved_source_updates(
            [(2, row)],
            [finding],
            approved_company_ids={"example"},
            sheet_client=client,
        )

        assert len(updates) == 1
        updated = client.updates[0][2]
        assert updated["source_slug"] == expected_slug
        assert updated["source_type"] == platform
        assert updates[0]["before"]["source_slug"] == ""
        assert updates[0]["after"]["source_slug"] == expected_slug


def test_structured_ats_conversion_refuses_unusable_slug():
    row = _company(source_url="https://example.com/careers", source_slug="")
    finding = _finding(
        source_url=row["source_url"],
        final_url="https://example.com/careers",
        classification=STRUCTURED_ATS,
        ats_platform="greenhouse",
        http_status=200,
        retry_eligible=False,
        requires_configuration_change=True,
        failure_observations=0,
        recommended_action="prefer_structured_ats",
    )
    client = FakeUpdateClient()

    updates = apply_approved_source_updates(
        [(2, row)],
        [finding],
        approved_company_ids={"example"},
        sheet_client=client,
    )

    assert updates == []
    assert client.updates == []


def test_no_probe_report_preserves_authoritative_source_audit(monkeypatch):
    events = []

    class FakeClient:
        def read_records_with_row_numbers(self, worksheet_name):
            return [(2, _company())]

        def read_records(self, worksheet_name):
            return []

        def append_run(self, record):
            events.append(("append_run", record))

    monkeypatch.setattr("src.source_quality_report.build_source_yield_report", lambda **kwargs: [])
    monkeypatch.setattr("src.source_quality_report.configured_zero_yield_rows", lambda **kwargs: [])
    monkeypatch.setattr(
        "src.source_quality_report.write_source_quality_surfaces",
        lambda *args, **kwargs: events.append(("write_surfaces", kwargs["write_audit"])) or {
            "source_audit_rows_written": 0,
            "source_yield_rows_written": 0,
        },
    )

    result = run_source_quality_report(
        probe_sources=False,
        write_report=True,
        sheet_client=FakeClient(),
    )

    assert events[0] == ("write_surfaces", False)
    assert result["source_audit_preserved"] is True
    assert result["source_audit_rows_written"] == 0


def test_no_probe_cleanup_is_rejected_before_writes():
    class FakeClient:
        def read_records_with_row_numbers(self, worksheet_name):
            return [(2, _company())]

        def read_records(self, worksheet_name):
            return []

    try:
        run_source_quality_report(
            probe_sources=False,
            write_report=True,
            approved_company_ids={"example"},
            sheet_client=FakeClient(),
        )
    except ValueError as exc:
        assert "requires live source probes" in str(exc)
    else:
        raise AssertionError("Expected no-probe cleanup to be rejected")

def test_legacy_no_probe_cleanup_is_rejected_before_writes():
    class FakeClient:
        def read_records_with_row_numbers(self, worksheet_name):
            return [(2, _company())]

        def read_records(self, worksheet_name):
            return []

    try:
        run_source_quality(
            probe_sources=False,
            write_report=True,
            approved_company_ids={"example"},
            sheet_client=FakeClient(),
        )
    except ValueError as exc:
        assert "requires live source probes" in str(exc)
    else:
        raise AssertionError("Expected legacy no-probe cleanup to be rejected")

