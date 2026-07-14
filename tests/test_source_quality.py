from __future__ import annotations

from datetime import UTC, date, datetime

import requests

from src.source_quality import (
    AUTH_OR_BOT_PROTECTION,
    DNS_FAILURE,
    EMPTY_VALID,
    HEALTHY,
    PERMANENT_404,
    REDIRECT_REQUIRED,
    STRUCTURED_ATS,
    TEMPORARILY_BLOCKED,
    SourceAuditFinding,
    SourceProbe,
    apply_approved_source_updates,
    audit_static_sources,
    build_source_yield_report,
    probe_source,
    retry_decision,
)


class FakeResponse:
    def __init__(self, status_code=200, text="", url="https://example.com/careers", history=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.history = list(history or [])
        self.headers = {}


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def get(self, url, *, timeout, headers, allow_redirects):
        if self.error is not None:
            raise self.error
        return self.response


class FakeSheetClient:
    def __init__(self):
        self.updates = []

    def update_record(self, worksheet_name, row_number, record):
        self.updates.append((worksheet_name, row_number, dict(record)))


def test_probe_classifies_healthy_page_with_visible_job_signal():
    session = FakeSession(FakeResponse(text="<div>Current openings</div>"))

    result = probe_source(
        "https://example.com/careers",
        session=session,
        observed_at="2026-07-14T12:00:00Z",
    )

    assert result.classification == HEALTHY
    assert result.has_job_signal is True
    assert result.http_status == 200


def test_probe_classifies_empty_but_valid_page():
    result = probe_source(
        "https://example.com/careers",
        session=FakeSession(FakeResponse(text="Welcome to our careers site")),
    )

    assert result.classification == EMPTY_VALID


def test_probe_classifies_redirect_replacement():
    response = FakeResponse(
        text="Open positions",
        url="https://careers.example.com/jobs",
        history=[FakeResponse(status_code=301)],
    )

    result = probe_source("https://example.com/old-careers", session=FakeSession(response))

    assert result.classification == REDIRECT_REQUIRED
    assert result.final_url == "https://careers.example.com/jobs"


def test_probe_prefers_structured_ats_before_static_scraping():
    response = FakeResponse(
        text="Open roles",
        url="https://jobs.lever.co/example",
        history=[FakeResponse(status_code=302)],
    )

    result = probe_source("https://example.com/careers", session=FakeSession(response))

    assert result.classification == STRUCTURED_ATS
    assert result.detected_ats == "lever"


def test_one_404_uses_cooldown_without_configuration_change():
    probe = SourceProbe(
        source_url="https://example.com/jobs",
        final_url="https://example.com/jobs",
        classification=PERMANENT_404,
        http_status=404,
    )

    decision = retry_decision(
        probe,
        failure_observations=1,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert decision.retry_eligible is True
    assert decision.requires_configuration_change is False
    assert decision.retry_after.startswith("2026-07-21")


def test_repeated_404_requires_configuration_change_before_retry():
    probe = SourceProbe(
        source_url="https://example.com/jobs",
        final_url="https://example.com/jobs",
        classification=PERMANENT_404,
        http_status=404,
    )

    decision = retry_decision(
        probe,
        failure_observations=2,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert decision.retry_eligible is False
    assert decision.requires_configuration_change is True


def test_dns_failures_use_cooldown_and_bounded_retry():
    probe = probe_source(
        "https://missing.example/jobs",
        session=FakeSession(error=requests.ConnectionError("Failed to resolve host: Name resolution")),
    )

    first = retry_decision(
        probe,
        failure_observations=1,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )
    third = retry_decision(
        probe,
        failure_observations=3,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert probe.classification == DNS_FAILURE
    assert first.retry_eligible is True
    assert first.requires_configuration_change is False
    assert third.retry_eligible is False
    assert third.requires_configuration_change is True


def test_403_remains_recoverable_after_one_observation():
    probe = probe_source(
        "https://example.com/jobs",
        session=FakeSession(FakeResponse(status_code=403, text="Forbidden")),
    )
    decision = retry_decision(
        probe,
        failure_observations=1,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert probe.classification == AUTH_OR_BOT_PROTECTION
    assert decision.retry_eligible is True
    assert decision.requires_configuration_change is False
    assert decision.retry_after.startswith("2026-07-28")


def test_temporary_server_failure_uses_bounded_cooldown():
    probe = probe_source(
        "https://example.com/jobs",
        session=FakeSession(FakeResponse(status_code=503, text="Unavailable")),
    )
    decision = retry_decision(
        probe,
        failure_observations=3,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert probe.classification == TEMPORARILY_BLOCKED
    assert decision.retry_eligible is True
    assert decision.retry_after.startswith("2026-07-21")


def test_audit_uses_run_history_to_confirm_repeated_404():
    companies = [
        {
            "company_id": "retired",
            "company_name": "Retired Co",
            "source_type": "static_page",
            "source_url": "https://retired.example/jobs",
            "active": "TRUE",
        }
    ]
    runs = [
        {
            "source_type": "static_page",
            "source_name": "static_page:Retired Co",
            "status": "failed",
            "finished_at": "2026-07-10T12:00:00Z",
            "notes": "url=https://retired.example/jobs; http_status=404",
        }
    ]

    findings = audit_static_sources(
        companies,
        runs=runs,
        session=FakeSession(
            FakeResponse(
                status_code=404,
                text="Not found",
                url="https://retired.example/jobs",
            )
        ),
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert findings[0].classification == PERMANENT_404
    assert findings[0].failure_observations == 2
    assert findings[0].requires_configuration_change is True
    assert findings[0].retry_eligible is False


def test_approved_redirect_update_preserves_history_and_requires_exact_id():
    row = {
        "company_id": "example",
        "company_name": "Example Co",
        "source_url": "https://example.com/old",
        "source_quality": "failed",
        "notes": "Original audit history",
    }
    finding = SourceAuditFinding(
        company_id="example",
        company_name="Example Co",
        source_url="https://example.com/old",
        final_url="https://careers.example.com/jobs",
        source_type="static_page",
        ats_platform="",
        classification=REDIRECT_REQUIRED,
        http_status=200,
        retry_eligible=False,
        retry_after="",
        requires_configuration_change=True,
        failure_observations=0,
        recommended_action="replace_source_url",
        recommendation_reason="Validated redirect",
        observed_at="2026-07-14T12:00:00Z",
    )
    client = FakeSheetClient()

    assert apply_approved_source_updates(
        [(2, row)],
        [finding],
        approved_company_ids={"other"},
        sheet_client=client,
    ) == []

    updates = apply_approved_source_updates(
        [(2, row)],
        [finding],
        approved_company_ids={"example"},
        sheet_client=client,
    )

    assert len(updates) == 1
    updated = client.updates[0][2]
    assert updated["source_url"] == "https://careers.example.com/jobs"
    assert "Original audit history" in updated["notes"]
    assert "Sprint 51 approved source update" in updated["notes"]


def test_repeated_404_is_retired_only_after_explicit_approval():
    row = {
        "company_id": "retired",
        "company_name": "Retired Co",
        "source_url": "https://retired.example/jobs",
        "source_quality": "failed",
        "ingestion_mode": "static_direct",
        "active": "TRUE",
        "notes": "Historical status retained",
    }
    finding = SourceAuditFinding(
        company_id="retired",
        company_name="Retired Co",
        source_url=row["source_url"],
        final_url=row["source_url"],
        source_type="static_page",
        ats_platform="",
        classification=PERMANENT_404,
        http_status=404,
        retry_eligible=False,
        retry_after="",
        requires_configuration_change=True,
        failure_observations=2,
        recommended_action="replace_or_retire_source",
        recommendation_reason="Repeated 404",
        observed_at="2026-07-14T12:00:00Z",
    )
    client = FakeSheetClient()

    apply_approved_source_updates(
        [(2, row)],
        [finding],
        approved_company_ids={"retired"},
        sheet_client=client,
    )

    updated = client.updates[0][2]
    assert updated["active"] == "FALSE"
    assert updated["ingestion_mode"] == "manual_review_only"
    assert updated["source_quality"] == "needs_manual_url_correction"
    assert "Historical status retained" in updated["notes"]


def test_four_week_yield_calculations_and_recommendations():
    jobs = [
        {
            "job_key": "job1",
            "company": "Example Co",
            "source_primary": "static_page",
            "status": "open",
            "potential_priority": "high",
            "potential_priority_score": "85",
            "review_status": "interested",
            "interest_decision": "interested",
            "verified_total_score": "84",
            "verified_alert_tier": "Strong Fit",
            "title": "Senior Manager, Strategy",
        },
        {
            "job_key": "job2",
            "company": "Example Co",
            "source_primary": "static_page",
            "status": "open",
            "potential_priority": "medium",
            "potential_priority_score": "65",
            "review_status": "dismissed",
            "interest_decision": "dismissed",
            "title": "Director, Strategy [Stretch Fit]",
        },
    ]
    sources = [
        {
            "source_key": "s1",
            "job_key": "job1",
            "company": "Example Co",
            "source_primary": "static_page",
            "source_type": "static_page",
            "source_url": "https://example.com/jobs",
            "first_seen_date": "2026-07-01",
        },
        {
            "source_key": "s2",
            "job_key": "job2",
            "company": "Example Co",
            "source_primary": "static_page",
            "source_type": "static_page",
            "source_url": "https://example.com/jobs",
            "first_seen_date": "2026-07-02",
        },
    ]
    rejected = [
        {
            "rejected_id": "r1",
            "source": "gmail_alert",
            "subject": "Dallas strategy jobs",
            "company": "Blocked Consulting",
            "title": "Senior Manager",
            "rejection_reason": "blocked_company consulting firm",
            "received_date": "2026-07-03",
        },
        {
            "rejected_id": "r2",
            "source": "gmail_alert",
            "subject": "Dallas strategy jobs",
            "company": "Other Co",
            "title": "Senior Director",
            "rejection_reason": "role_too_senior",
            "received_date": "2026-07-04",
        },
    ]

    rows = build_source_yield_report(
        jobs=jobs,
        job_sources=sources,
        rejected_jobs=rejected,
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    static_row = next(row for row in rows if row.group_type == "static_company_source")
    gmail_row = next(
        row
        for row in rows
        if row.group_type == "gmail_alert_or_search"
        and row.group_key == "Dallas strategy jobs"
    )

    assert static_row.leads_received == 2
    assert static_row.jobs_accepted == 2
    assert static_row.surfaced_for_review == 1
    assert static_row.manually_dismissed == 1
    assert static_row.interested == 1
    assert static_row.strong_fit_count == 1
    assert static_row.stretch_fit_count == 1
    assert static_row.average_potential_score == 75.0
    assert static_row.review_yield_percent == 100.0
    assert gmail_row.leads_received == 2
    assert gmail_row.auto_rejected == 2
    assert gmail_row.blocked_company_rejects == 1
    assert gmail_row.too_senior_rejects == 1


def test_low_volume_strategic_source_is_not_retired():
    rows = build_source_yield_report(
        jobs=[
            {
                "job_key": "job1",
                "company": "Strategic Co",
                "source_primary": "static_page",
                "status": "open",
                "potential_priority": "low",
                "review_status": "not_reviewed",
            }
        ],
        job_sources=[
            {
                "source_key": "s1",
                "job_key": "job1",
                "company": "Strategic Co",
                "source_primary": "static_page",
                "source_type": "static_page",
                "source_url": "https://strategic.example/jobs",
                "first_seen_date": "2026-07-01",
            }
        ],
        rejected_jobs=[],
        target_companies=[{"company_name": "Strategic Co", "active": "TRUE"}],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    row = next(item for item in rows if item.group_type == "static_company_source")
    assert row.strategic_target is True
    assert row.recommendation == "keep"
