from __future__ import annotations

from src.source_audit import (
    DISABLED,
    GMAIL_ONLY,
    MANUAL_REVIEW_ONLY,
    STATIC_DIRECT,
    ATS_GREENHOUSE,
    NEEDS_MANUAL_URL_CORRECTION,
    SUCCESS,
    TOO_NOISY,
    DISABLE_RECOMMENDED,
    audit_source_configuration,
    classify_source_row,
    source_audit_record_updates,
    summarize_source_audit,
)


def row(**overrides):
    values = {
        "company_id": "acme",
        "company_name": "Acme Industrial",
        "source_type": "static_page",
        "source_slug": "",
        "source_url": "https://www.acme.example/careers",
        "ats_platform": "custom",
        "location_focus": "Plano, TX",
        "industry_bucket": "manufacturing",
        "company_size_bucket": "large",
        "ownership_type": "PE-backed",
        "priority_tier": "Tier 1",
        "source_quality": "",
        "ingestion_mode": "",
        "active": "TRUE",
        "notes": "",
    }
    values.update(overrides)
    return values


def test_static_company_career_page_is_static_direct_success():
    finding = classify_source_row(row())

    assert finding.audit_status == SUCCESS
    assert finding.recommended_ingestion_mode == STATIC_DIRECT


def test_greenhouse_source_uses_ats_mode():
    finding = classify_source_row(
        row(
            company_name="Greenhouse Co",
            source_type="greenhouse",
            ats_platform="greenhouse",
            source_url="https://boards.greenhouse.io/acme",
        )
    )

    assert finding.audit_status == SUCCESS
    assert finding.recommended_ingestion_mode == ATS_GREENHOUSE


def test_job_boards_are_not_static_sources():
    linkedin = classify_source_row(row(company_name="LinkedIn", source_url="https://www.linkedin.com/jobs/search/?keywords=strategy"))
    indeed = classify_source_row(row(company_name="Indeed", source_url="https://www.indeed.com/jobs?q=strategy&l=Dallas"))
    builtin = classify_source_row(row(company_name="Built In", source_url="https://builtin.com/jobs"))

    assert linkedin.audit_status == TOO_NOISY
    assert indeed.audit_status == TOO_NOISY
    assert builtin.audit_status == TOO_NOISY
    assert linkedin.recommended_ingestion_mode == GMAIL_ONLY
    assert indeed.recommended_ingestion_mode == GMAIL_ONLY
    assert builtin.recommended_ingestion_mode == GMAIL_ONLY


def test_ladders_is_disabled():
    finding = classify_source_row(row(company_name="The Ladders", source_url="https://www.theladders.com/jobs/search-jobs?keywords=project+manager"))

    assert finding.audit_status == DISABLE_RECOMMENDED
    assert finding.recommended_ingestion_mode == DISABLED


def test_known_failed_company_sources_are_marked_for_manual_correction():
    fossil = classify_source_row(row(company_name="Fossil Group", source_url="https://www.fossilgroup.com/careers"))
    lennox = classify_source_row(row(company_name="Lennox", source_url="https://broken-lennox.example/jobs"))
    toyota = classify_source_row(row(company_name="Toyota Financial Services", source_url="https://example.com/not-found"))
    mary_kay = classify_source_row(row(company_name="Mary Kay", source_url="https://example.com/not-found"))

    assert fossil.recommended_ingestion_mode == MANUAL_REVIEW_ONLY
    assert lennox.audit_status == NEEDS_MANUAL_URL_CORRECTION
    assert toyota.audit_status == NEEDS_MANUAL_URL_CORRECTION
    assert mary_kay.audit_status == NEEDS_MANUAL_URL_CORRECTION


def test_search_or_navigation_source_needs_manual_url_correction():
    finding = classify_source_row(row(source_url="https://www.acme.example/jobs/search?query=strategy&location=Dallas"))

    assert finding.audit_status == NEEDS_MANUAL_URL_CORRECTION
    assert finding.recommended_ingestion_mode == MANUAL_REVIEW_ONLY


def test_audit_summary_counts_statuses_and_modes():
    findings = audit_source_configuration(
        [
            row(company_name="Acme Industrial"),
            row(company_name="LinkedIn", source_url="https://www.linkedin.com/jobs/search/?keywords=strategy"),
            row(company_name="The Ladders", source_url="https://www.theladders.com/jobs/search-jobs?keywords=project+manager"),
        ]
    )
    summary = summarize_source_audit(findings)

    assert summary["sources_audited"] == 3
    assert summary["issue_count"] == 2
    assert summary["status_counts"][SUCCESS] == 1
    assert summary["recommended_ingestion_mode_counts"][STATIC_DIRECT] == 1
    assert summary["recommended_ingestion_mode_counts"][GMAIL_ONLY] == 1
    assert summary["recommended_ingestion_mode_counts"][DISABLED] == 1


def test_recommendation_updates_source_quality_ingestion_mode_and_active_flag():
    finding = classify_source_row(row(company_name="The Ladders", source_url="https://www.theladders.com/jobs/search-jobs?keywords=project+manager"))
    updated = source_audit_record_updates(row(), finding)

    assert updated["source_quality"] == DISABLE_RECOMMENDED
    assert updated["ingestion_mode"] == DISABLED
    assert updated["active"] == "FALSE"
    assert "Sprint 18 source audit" in updated["notes"]
