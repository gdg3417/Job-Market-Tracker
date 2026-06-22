from __future__ import annotations

from src.data_quality import job_url_rejection_reason, validate_job_quality
from src.normalize import normalize_raw_job


def make_job(title="Director, Commercial Strategy", company="Acme Industrial", url="https://careers.acme.example/jobs/director-commercial-strategy-12345", source="static_page", description="Own commercial strategy, revenue growth, and executive cadence."):
    return normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": "Dallas, TX",
            "url": url,
            "source_job_id": url,
            "description": description,
        },
        source_primary=source,
        seen_date="2026-06-16",
    )


def test_alert_header_titles_are_rejected():
    bad_titles = [
        "Job Search Search Jobs...",
        "Jobs Near Me Jobs in my city",
        "New jobs match your preferences.",
        "Your job alert for project manager in Dallas",
        "Your job alert has been created: Revenue Strategy in Dallas, Texas, United States.",
    ]

    for title in bad_titles:
        reasons = validate_job_quality(make_job(title=title, url="https://www.linkedin.com/jobs/view/4242424242", source="gmail_alert"))
        assert "generic_alert_or_search_title" in reasons or "title_looks_like_alert_metadata" in reasons


def test_alert_confirmation_company_text_is_rejected():
    reasons = validate_job_quality(
        make_job(
            title="Revenue Strategy Senior Manager / Director",
            company="You’ll receive notifications when new jobs are posted that match your search preferences.",
            url="https://www.linkedin.com/jobs/view/4242424242",
            source="gmail_alert",
        )
    )

    assert "company_looks_like_alert_metadata" in reasons


def test_generic_board_and_tracking_urls_are_rejected():
    assert job_url_rejection_reason("https://www.theladders.com/jobs/search-jobs?keywords=project+manager&location=Dallas", "static_page")
    assert job_url_rejection_reason("https://static.licdn.com/sc/h/abc123.png", "gmail_alert") == "tracking_or_static_asset_host"
    assert job_url_rejection_reason("https://www.linkedin.com/jobs/search/?keywords=project%20manager&location=Dallas", "gmail_alert")


def test_direct_posting_urls_are_accepted():
    assert job_url_rejection_reason("https://www.linkedin.com/jobs/view/4242424242/?trackingId=abc", "gmail_alert") == ""
    assert job_url_rejection_reason("https://careers.acme.example/jobs/director-commercial-strategy-12345", "static_page") == ""
    assert job_url_rejection_reason("https://jobs.lever.co/acme/8f9a7b6c5d4e3f2a1b", "lever") == ""


def test_manual_review_rows_require_trusted_static_direct_posting():
    accepted = make_job(description="Static extraction confidence: low. manual_review=true.")
    rejected = make_job(url="https://builtin.com/jobs", description="Static extraction confidence: low. manual_review=true.")

    assert validate_job_quality(accepted) == []
    reasons = validate_job_quality(rejected)
    assert "manual_review_job_not_trusted_static_direct_posting" in reasons


def test_sparse_gmail_review_with_direct_linkedin_posting_passes_quality_gate():
    job = make_job(
        title="Sr Manager, Strategic Planning",
        company="Topgolf",
        url="https://www.linkedin.com/jobs/view/4417965465",
        source="gmail_alert",
        description="Extracted from Gmail job alert.",
    )
    job.score_explanation = "total=20; tier=ignore; manual_review=true; review_reason=sparse_gmail_high_signal_title"

    assert validate_job_quality(job) == []


def test_sparse_gmail_review_still_requires_direct_posting_url():
    job = make_job(
        title="Sr Manager, Strategic Planning",
        company="Topgolf",
        url="https://www.linkedin.com/jobs/search",
        source="gmail_alert",
        description="Extracted from Gmail job alert.",
    )
    job.score_explanation = "total=20; tier=ignore; manual_review=true; review_reason=sparse_gmail_high_signal_title"

    reasons = validate_job_quality(job)
    assert "generic_job_board_or_career_navigation_page" in reasons
    assert "manual_review_job_not_trusted_static_direct_posting" in reasons


def test_unrecognized_gmail_manual_review_reason_stays_rejected():
    job = make_job(
        title="Sr Manager, Strategic Planning",
        company="Topgolf",
        url="https://www.linkedin.com/jobs/view/4417965465",
        source="gmail_alert",
        description="Extracted from Gmail job alert.",
    )
    job.score_explanation = "manual_review=true; review_reason=other"

    reasons = validate_job_quality(job)
    assert "manual_review_job_not_trusted_static_direct_posting" in reasons
