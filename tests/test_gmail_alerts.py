import pytest

from src.sources.gmail_alerts import (
    GmailAlertEmail,
    alert_to_raw_job,
    alert_to_rejected_job_record,
    extract_urls,
    is_supported_job_url,
    parse_job_alert_email,
    parse_job_alert_text,
    parsed_alerts_to_jobs,
    received_date_from_header,
    should_upsert_alert,
)


def test_extract_urls_normalizes_tracking_params():
    urls = extract_urls("Apply: https://www.linkedin.com/jobs/view/123/?utm_source=alert&foo=bar")
    assert urls == ["https://www.linkedin.com/jobs/view/123?foo=bar"]


def test_parse_job_alert_text_extracts_title_company_location_and_url():
    body = """
    Senior Manager, Revenue Strategy
    Acme Manufacturing
    Plano, TX
    https://www.linkedin.com/jobs/view/123?utm_source=job_alert
    """
    alerts = parse_job_alert_text("LinkedIn Job Alert", body)
    assert len(alerts) == 1
    assert alerts[0].title == "Senior Manager, Revenue Strategy"
    assert alerts[0].company == "Acme Manufacturing"
    assert alerts[0].location == "Plano, TX"
    assert alerts[0].url == "https://www.linkedin.com/jobs/view/123"
    assert alerts[0].source == "gmail_alert"
    assert alerts[0].confidence == "high"
    assert alerts[0].is_rejected is False
    assert should_upsert_alert(alerts[0]) is True


def test_parse_job_alert_email_handles_single_line_pattern():
    email = GmailAlertEmail(
        message_id="abc123",
        subject="Indeed Job Alert",
        sender="jobs@indeed.com",
        received_at="Tue, 16 Jun 2026 08:30:00 -0500",
        body_text="Director, Commercial Strategy at Fossil Group - Richardson, TX\nhttps://example.com/jobs/42",
    )
    alerts = parse_job_alert_email(email)
    assert len(alerts) == 1
    assert alerts[0].title == "Director, Commercial Strategy"
    assert alerts[0].company == "Fossil Group"
    assert alerts[0].location == "Richardson, TX"
    assert alerts[0].received_date == "2026-06-16"
    assert alerts[0].source_job_id.startswith("gmail-")
    assert alerts[0].is_rejected is False


def test_low_confidence_alert_is_quarantined_not_converted_to_job():
    alerts = parse_job_alert_text("Google Alert", "A job might match you\nhttps://example.com/job")
    assert len(alerts) == 1
    assert alerts[0].is_rejected is True
    assert alerts[0].confidence == "rejected"
    assert "title_lacks_role_signal" in alerts[0].rejection_reason or alerts[0].rejection_reason
    assert parsed_alerts_to_jobs(alerts, seen_date="2026-06-16") == []
    rejected = alert_to_rejected_job_record(alerts[0])
    assert rejected["rejection_reason"]
    with pytest.raises(ValueError):
        alert_to_raw_job(alerts[0], seen_date="2026-06-16")


def test_linkedin_premium_upsell_link_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Senior Manager, Revenue Strategy
        Acme Manufacturing
        Plano, TX
        https://www.linkedin.com/premium/products/?trk=eml-email
        """,
    )
    assert alerts[0].is_rejected is True
    assert "premium" in alerts[0].rejection_reason
    assert parsed_alerts_to_jobs(alerts) == []


def test_linkedin_help_link_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Director, Commercial Strategy
        Fossil Group
        Richardson, TX
        https://www.linkedin.com/help/linkedin/answer/a507663
        """,
    )
    assert alerts[0].is_rejected is True
    assert "help" in alerts[0].rejection_reason


def test_linkedin_unsubscribe_link_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Senior Manager, Business Operations
        Acme Manufacturing
        Dallas, TX
        https://www.linkedin.com/comm/email/unsubscribe?midToken=AQF
        """,
    )
    assert alerts[0].is_rejected is True
    assert "unsubscribe" in alerts[0].rejection_reason or "utility" in alerts[0].rejection_reason


def test_linkedin_alert_management_link_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Director, Revenue Strategy
        Example Co
        Remote
        https://www.linkedin.com/jobs/alerts/
        """,
    )
    assert alerts[0].is_rejected is True
    assert "jobs/alerts" in alerts[0].rejection_reason


def test_linkedin_static_asset_link_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Senior Manager, Commercial Strategy
        Acme Manufacturing
        Plano, TX
        https://static.licdn.com/sc/h/abc123.png
        """,
    )
    assert alerts[0].is_rejected is True
    assert alerts[0].rejection_reason == "static_asset_tracking_or_utility_url"


def test_w3_org_and_assets_are_not_supported_job_urls():
    assert is_supported_job_url("https://www.w3.org/TR/PNG/", "linkedin") is False
    assert is_supported_job_url("https://example.com/assets/logo.svg", "unknown_alert_source") is False


def test_swapped_location_and_company_is_rejected():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Senior Manager, Revenue Strategy
        Plano, TX
        Acme Manufacturing
        https://www.linkedin.com/jobs/view/123
        """,
    )
    assert alerts[0].is_rejected is True
    assert alerts[0].rejection_reason == "company_looks_like_location"
    assert parsed_alerts_to_jobs(alerts) == []


def test_footer_text_is_rejected_not_interpreted_as_job():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        LinkedIn Corporation
        Help Center
        Manage alerts
        https://www.linkedin.com/help/linkedin
        """,
    )
    assert alerts[0].is_rejected is True
    assert parsed_alerts_to_jobs(alerts) == []


def test_valid_linkedin_jobs_view_record_is_accepted():
    alerts = parse_job_alert_text(
        "LinkedIn Job Alert",
        """
        Director, Business Operations
        Texas Instruments
        Dallas, TX
        https://www.linkedin.com/jobs/view/4242424242/?trackingId=abc&utm_campaign=job_alert
        """,
    )
    assert len(alerts) == 1
    assert alerts[0].is_rejected is False
    assert alerts[0].url == "https://www.linkedin.com/jobs/view/4242424242?trackingId=abc"
    jobs = parsed_alerts_to_jobs(alerts, seen_date="2026-06-16")
    assert len(jobs) == 1
    assert jobs[0].company == "Texas Instruments"
    assert jobs[0].title == "Director, Business Operations"


def test_received_date_from_header_defaults_safely():
    assert received_date_from_header("not a real date")
