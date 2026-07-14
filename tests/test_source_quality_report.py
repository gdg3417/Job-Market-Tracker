from __future__ import annotations

from datetime import date

from src.source_quality import SourceYieldRow
from src.source_quality_report import configured_zero_yield_rows


def test_zero_result_static_source_is_reported():
    rows = configured_zero_yield_rows(
        company_rows=[
            {
                "company_id": "example",
                "company_name": "Example Co",
                "source_type": "static_page",
                "source_url": "https://example.com/jobs",
                "active": "TRUE",
            }
        ],
        search_rows=[],
        target_companies=[],
        existing_rows=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    static_row = next(row for row in rows if row.group_type == "static_company_source")
    assert static_row.group_key == "Example Co | https://example.com/jobs"
    assert static_row.leads_received == 0
    assert static_row.recommendation == "review_or_reduce_cadence"


def test_zero_result_configured_search_is_reported():
    rows = configured_zero_yield_rows(
        company_rows=[],
        search_rows=[
            {
                "search_id": "dallas_strategy_manager",
                "bucket": "Dallas strategy",
                "active": "TRUE",
            }
        ],
        target_companies=[],
        existing_rows=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.group_type == "gmail_alert_or_search"
    assert row.group_key == "dallas_strategy_manager"
    assert row.leads_received == 0
    assert row.source_type == "configured_search"


def test_inactive_zero_result_configuration_is_omitted():
    rows = configured_zero_yield_rows(
        company_rows=[
            {
                "company_id": "inactive",
                "company_name": "Inactive Co",
                "source_type": "static_page",
                "source_url": "https://inactive.example/jobs",
                "active": "FALSE",
            }
        ],
        search_rows=[{"search_id": "inactive_search", "active": "FALSE"}],
        target_companies=[],
        existing_rows=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    assert rows == []


def test_strategic_zero_result_source_keeps_coverage():
    rows = configured_zero_yield_rows(
        company_rows=[
            {
                "company_id": "strategic",
                "company_name": "Strategic Co",
                "source_type": "static_page",
                "source_url": "https://strategic.example/jobs",
                "active": "TRUE",
            }
        ],
        search_rows=[],
        target_companies=[{"company_name": "Strategic Co", "active": "TRUE"}],
        existing_rows=[],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    static_row = next(row for row in rows if row.group_type == "static_company_source")
    assert static_row.strategic_target is True
    assert static_row.recommendation == "keep_strategic_coverage"


def test_existing_observed_source_is_not_duplicated_as_zero_result():
    existing = SourceYieldRow(
        window_start="2026-06-17",
        window_end="2026-07-14",
        group_type="static_company_source",
        group_key="Example Co | https://example.com/jobs",
        source_type="static_page",
        company="Example Co",
        strategic_target=False,
        leads_received=2,
        jobs_accepted=1,
        auto_rejected=1,
        blocked_company_rejects=0,
        too_junior_rejects=0,
        too_senior_rejects=1,
        surfaced_for_review=1,
        manually_dismissed=0,
        interested=0,
        applied=0,
        strong_fit_count=0,
        stretch_fit_count=1,
        average_potential_score=65.0,
        review_yield_percent=0.0,
        actionable_conversion_percent=0.0,
        recommendation="keep",
        recommendation_reason="Observed source",
    )

    rows = configured_zero_yield_rows(
        company_rows=[
            {
                "company_id": "example",
                "company_name": "Example Co",
                "source_type": "static_page",
                "source_url": "https://example.com/jobs",
                "active": "TRUE",
            }
        ],
        search_rows=[],
        target_companies=[],
        existing_rows=[existing],
        weeks=4,
        as_of=date(2026, 7, 14),
    )

    assert all(row.group_type != "static_company_source" for row in rows)
    assert any(row.group_type == "company" for row in rows)
