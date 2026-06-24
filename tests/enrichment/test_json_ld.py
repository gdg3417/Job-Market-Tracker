from __future__ import annotations

from src.enrichment.extractors import extract_job_evidence
from src.enrichment.fetcher import FetchResult
from src.enrichment.json_ld import best_job_posting


def job_html() -> str:
    return """
    <html><head><link rel="canonical" href="https://careers.example.com/jobs/123"></head><body>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Senior Manager, Strategic Planning",
      "hiringOrganization": {"@type": "Organization", "name": "Topgolf"},
      "jobLocation": {"@type": "Place", "address": {"addressLocality": "Dallas", "addressRegion": "TX", "addressCountry": "US"}},
      "description": "<p>Lead strategic planning and manage a team. Responsibilities include growth planning. Qualifications include eight years of experience and a bachelor's degree.</p>",
      "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 150000, "maxValue": 180000, "unitText": "YEAR"}},
      "employmentType": "FULL_TIME",
      "datePosted": "2026-06-20",
      "validThrough": "2026-07-20",
      "url": "https://careers.example.com/jobs/123"
    }
    </script></body></html>
    """


def test_valid_json_ld_job_posting_is_extracted():
    posting = best_job_posting(job_html())
    assert posting is not None
    assert posting["source_title"] == "Senior Manager, Strategic Planning"
    assert posting["source_company"] == "Topgolf"
    assert posting["source_location"] == "Dallas, TX, US"
    assert posting["salary_min"] == 150000
    assert posting["salary_max"] == 180000
    assert posting["currency"] == "USD"


def test_evidence_does_not_store_raw_html_and_captures_hash():
    result = FetchResult(
        requested_url="https://lead.example/123",
        final_url="https://careers.example.com/jobs/123",
        status_code=200,
        content_type="text/html",
        text=job_html(),
    )
    evidence = extract_job_evidence(result, job_key="job-1", enrichment_id="enr-1")
    assert evidence is not None
    assert evidence.description_text.startswith("Lead strategic planning")
    assert "<script" not in evidence.description_text
    assert len(evidence.raw_content_hash) == 64
    assert evidence.team_leadership_text.startswith("Lead strategic planning")


def test_non_job_page_is_rejected():
    result = FetchResult(
        requested_url="https://example.com/careers",
        final_url="https://example.com/careers",
        status_code=200,
        content_type="text/html",
        text="<html><head><title>Careers</title></head><body><h1>Careers</h1><p>Join our talent community.</p></body></html>",
    )
    assert extract_job_evidence(result, job_key="job-1", enrichment_id="enr-1") is None
