from __future__ import annotations

import pytest

from src.enrichment.matcher import assess_match
from src.enrichment.merge import merge_verified_evidence
from src.enrichment.models import EnrichmentEvidence
from src.models import JobPosting


def job(**overrides):
    values = {
        "job_key": "job-1",
        "company": "Toyota North America",
        "title": "National Manager, Product",
        "location": "Plano, TX",
        "canonical_url": "https://linkedin.example/jobs/123",
        "source_job_id": "123",
        "potential_priority": "high",
        "score_status": "provisional",
        "enrichment_status": "pending",
    }
    values.update(overrides)
    return JobPosting(**values)


def evidence(**overrides):
    values = {
        "source_url": "https://careers.toyota.com/jobs/123",
        "canonical_url": "https://careers.toyota.com/jobs/123",
        "source_title": "National Manager, Product",
        "source_company": "Toyota North America",
        "source_location": "Plano, TX, US",
        "description_text": "Lead product strategy, manage a team, and own national growth planning. Qualifications include ten years of experience.",
        "remote_status": "hybrid",
        "work_model": "hybrid",
        "raw_content_hash": "abc",
    }
    values.update(overrides)
    return EnrichmentEvidence(**values)


def test_confident_match_merges_authoritative_fields_without_changing_identity():
    target = job(description_text="Extracted from Gmail job alert")
    source = evidence(salary_min=160000, salary_max=190000, currency="USD")
    match = assess_match(target, source)
    merged, changed = merge_verified_evidence(target, source, match_confidence=match.confidence)
    assert match.accepted is True
    assert merged.job_key == "job-1"
    assert merged.title == "National Manager, Product"
    assert merged.company == "Toyota North America"
    assert merged.description_text.startswith("Lead product strategy")
    assert merged.salary_min == 160000
    assert merged.canonical_url == "https://careers.toyota.com/jobs/123"
    assert merged.enrichment_status == "enriched"
    assert "description_text" in changed


def test_mismatched_title_is_not_accepted_or_mergeable():
    target = job()
    source = evidence(source_title="Staff Accountant")
    match = assess_match(target, source)
    assert match.accepted is False
    with pytest.raises(ValueError):
        merge_verified_evidence(target, source, match_confidence=match.confidence)


def test_mismatched_company_is_not_accepted():
    match = assess_match(job(), evidence(source_company="Unrelated Software Company"))
    assert match.accepted is False
    assert match.confidence < 80


def test_shorter_direct_description_does_not_replace_stronger_existing_description():
    existing = " ".join(["Detailed responsibilities and qualifications for strategic product leadership"] * 20)
    target = job(description_text=existing, salary_min=170000)
    source = evidence(description_text="Short description", salary_min=150000)
    merged, changed = merge_verified_evidence(target, source, match_confidence=90)
    assert merged.description_text == existing
    assert merged.salary_min == 170000
    assert "description_text" not in changed
    assert "salary_min" not in changed
