from src.sources.gmail_alerts import (
    GmailAlertEmail,
    alert_to_raw_job,
    extract_urls,
    parse_job_alert_email,
    parse_job_alert_text,
    received_date_from_header,
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


def test_low_confidence_alert_is_flagged_for_review():
    alerts = parse_job_alert_text("Google Alert", "A job might match you\nhttps://example.com/job")
    assert alerts[0].confidence == "low"
    raw_job = alert_to_raw_job(alerts[0], seen_date="2026-06-16")
    assert raw_job["company"] == "Unknown Company"
    assert raw_job["title"] == "Review Gmail job alert"
    assert "manual_review_required" in raw_job["description"]


def test_received_date_from_header_defaults_safely():
    assert received_date_from_header("not a real date")
