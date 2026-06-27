from __future__ import annotations

from src.enrichment.company_config import company_config_from_row
from src.models import JobPosting
from src.resolution.models import ResolutionCandidate
from src.resolution.scoring import ResolutionThresholds, score_candidate


def _job(**overrides):
    values = {
        "job_key": "job-1",
        "company": "Toyota North America",
        "title": "National Manager, Product",
        "location": "Plano, TX",
        "source_job_id": "REQ-123",
        "first_seen_date": "2026-06-22",
    }
    values.update(overrides)
    return JobPosting(**values)


def _candidate(**overrides):
    values = {
        "job_key": "job-1",
        "discovery_method": "configured_ats_board",
        "canonical_url": "https://careers.toyota.com/us/en/job/REQ-123/national-manager-product",
        "platform": "phenom",
        "stable_identifier": "REQ-123",
        "requisition_id": "REQ-123",
        "source_title": "National Manager, Product",
        "source_company": "Toyota Motor North America",
        "source_location": "Plano, TX",
        "posting_date": "2026-06-20",
    }
    values.update(overrides)
    return ResolutionCandidate(**values)


def _config():
    return company_config_from_row(
        {
            "company_name": "Toyota Motor North America",
            "canonical_company_name": "Toyota Motor North America",
            "company_aliases": "Toyota North America|Toyota",
            "career_domain": "careers.toyota.com",
            "career_search_url": "https://careers.toyota.com/us/search-results",
            "ats_platform": "phenom",
            "enrichment_active": True,
        }
    )


def test_exact_requisition_match_is_highly_weighted_and_authoritative():
    result = score_candidate(_job(), _candidate(), config=_config())

    assert result.requisition_match == 100
    assert result.company_match == 100
    assert result.title_match == 100
    assert result.confidence >= 90
    assert result.eligible_for_authoritative is True


def test_title_similarity_alone_cannot_pass_company_gate():
    result = score_candidate(
        _job(),
        _candidate(source_company="Different Corporation", requisition_id="", stable_identifier=""),
        config=_config(),
    )

    assert result.title_match == 100
    assert result.company_match < 75
    assert result.eligible_for_authoritative is False
    assert any(reason.startswith("company_match_below_threshold") for reason in result.reasons)


def test_location_disagreement_reduces_confidence_without_overriding_exact_requisition():
    result = score_candidate(_job(), _candidate(source_location="New York, NY"), config=_config())

    assert result.location_match < 70
    assert result.requisition_match == 100
    assert result.confidence >= ResolutionThresholds().authoritative
