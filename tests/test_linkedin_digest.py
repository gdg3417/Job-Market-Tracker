from pathlib import Path

from src.sources.eml import read_eml
from src.sources.gmail_alerts import GmailAlertEmail, parse_job_alert_email, should_upsert_alert

FIXTURES = Path(__file__).parent / "fixtures"


def _accepted_by_id(filename: str):
    email = read_eml(FIXTURES / filename)
    alerts = parse_job_alert_email(email)
    accepted = [alert for alert in alerts if should_upsert_alert(alert)]
    return email, alerts, {alert.source_job_id.removeprefix("linkedin-"): alert for alert in accepted}


def test_eml_reader_extracts_headers_and_bodies():
    email = read_eml(FIXTURES / "linkedin_topgolf.eml")

    assert email.subject == "Sr Manager, Strategic Planning at Topgolf"
    assert "jobalerts-noreply@linkedin.com" in email.sender
    assert email.received_at == "Mon, 22 Jun 2026 10:44:27 +0000"
    assert "4417965465" in email.body_text
    assert email.body_html == ""


def test_topgolf_fixture_produces_six_correct_job_cards():
    _, alerts, jobs = _accepted_by_id("linkedin_topgolf.eml")

    assert len(alerts) == 6
    assert len(jobs) == 6
    expected = {
        "4417965465": ("Sr Manager, Strategic Planning", "Topgolf", "Dallas, TX"),
        "4419927418": ("Director, Revenue Strategy", "Example Consumer Co", "Plano, TX"),
        "4428299479": ("Senior Manager, Commercial Operations", "Example Industrial Co", "Dallas, TX"),
        "4428297931": ("Manager, Business Strategy", "Example Retail Co", "Remote"),
        "4431335527": ("Director, Pricing Strategy", "Example Manufacturing Co", "Fort Worth, TX"),
        "4430057257": ("Senior Manager, Business Insights", "Example Technology Co", "Richardson, TX"),
    }
    assert set(jobs) == set(expected)
    for job_id, expected_fields in expected.items():
        alert = jobs[job_id]
        assert (alert.title, alert.company, alert.location) == expected_fields
        assert alert.url == f"https://www.linkedin.com/jobs/view/{job_id}"
        assert alert.source_job_id == f"linkedin-{job_id}"


def test_toyota_fixture_produces_six_correct_job_cards():
    _, alerts, jobs = _accepted_by_id("linkedin_toyota.eml")

    assert len(alerts) == 6
    assert len(jobs) == 6
    expected = {
        "4430066274": ("National Manager, Product", "Toyota North America", "Plano, TX"),
        "4430017649": ("Director, Product Strategy", "Example Mobility Co", "Dallas, TX"),
        "4421252459": ("Senior Manager, Product Line", "Example Automotive Co", "Plano, TX"),
        "4412157494": ("Manager, Category Strategy", "Example Equipment Co", "Irving, TX"),
        "4422518097": ("Director, Portfolio Management", "Example Manufacturing Co", "Fort Worth, TX"),
        "4430051710": ("Senior Manager, Business Operations", "Example Consumer Co", "Richardson, TX"),
    }
    assert set(jobs) == set(expected)
    for job_id, expected_fields in expected.items():
        alert = jobs[job_id]
        assert (alert.title, alert.company, alert.location) == expected_fields
        assert alert.url == f"https://www.linkedin.com/jobs/view/{job_id}"
        assert alert.source_job_id == f"linkedin-{job_id}"


def test_utility_links_and_duplicate_direct_links_do_not_create_records():
    _, alerts, _ = _accepted_by_id("linkedin_topgolf.eml")

    assert len(alerts) == 6
    assert len({alert.source_job_id for alert in alerts}) == 6
    assert all("jobs/search" not in alert.url for alert in alerts)
    assert all("premium" not in alert.url for alert in alerts)
    assert all("alerts" not in alert.url for alert in alerts)
    assert all("unsubscribe" not in alert.url for alert in alerts)
    assert all("help" not in alert.url for alert in alerts)


def test_same_linkedin_posting_has_same_source_id_across_emails():
    original = read_eml(FIXTURES / "linkedin_topgolf.eml")
    repeated = GmailAlertEmail(
        message_id="different-message-id",
        thread_id="different-thread-id",
        subject="Repeated digest",
        sender=original.sender,
        received_at="Tue, 23 Jun 2026 10:44:27 +0000",
        body_text=original.body_text,
    )

    first = {alert.url: alert.source_job_id for alert in parse_job_alert_email(original)}
    second = {alert.url: alert.source_job_id for alert in parse_job_alert_email(repeated)}

    assert first == second
    assert first["https://www.linkedin.com/jobs/view/4417965465"] == "linkedin-4417965465"


def test_malformed_card_is_rejected_without_rejecting_valid_card():
    email = GmailAlertEmail(
        message_id="mixed-digest",
        subject="LinkedIn job digest",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Mon, 22 Jun 2026 10:44:27 +0000",
        body_text="""
        [Example Co](https://www.linkedin.com/comm/jobs/view/4000000001/?trackingId=bad)

        [Valid Co](https://www.linkedin.com/comm/jobs/view/4000000002/?trackingId=logo)
        [Director, Business Operations
        Valid Co · Dallas, TX (Hybrid)](https://www.linkedin.com/comm/jobs/view/4000000002/?trackingId=card)
        """,
    )

    alerts = parse_job_alert_email(email)

    assert len(alerts) == 2
    by_id = {alert.source_job_id: alert for alert in alerts}
    assert by_id["linkedin-4000000001"].is_rejected is True
    assert by_id["linkedin-4000000001"].rejection_reason
    assert by_id["linkedin-4000000002"].is_rejected is False
    assert by_id["linkedin-4000000002"].title == "Director, Business Operations"


def test_html_is_used_when_plain_text_has_no_direct_jobs():
    email = GmailAlertEmail(
        message_id="html-digest",
        subject="LinkedIn job digest",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Mon, 22 Jun 2026 10:44:27 +0000",
        body_text="This plain-text alternative does not include the posting links.",
        body_html="""
        <html><body>
          <a href="https://www.linkedin.com/comm/jobs/view/5000000001/?trackingId=one">
            <div>Director, Commercial Strategy</div>
            <div>Example Co · Plano, TX (Hybrid)</div>
          </a>
          <a href="https://www.linkedin.com/comm/jobs/view/5000000002/?trackingId=two">
            <div>Senior Manager, Revenue Strategy</div>
            <div>Second Co · Dallas, TX (On-site)</div>
          </a>
          <a href="https://www.linkedin.com/jobs/alerts/">Manage alerts</a>
        </body></html>
        """,
    )

    alerts = parse_job_alert_email(email)

    assert len(alerts) == 2
    assert [alert.source_job_id for alert in alerts] == ["linkedin-5000000001", "linkedin-5000000002"]
    assert [alert.title for alert in alerts] == ["Director, Commercial Strategy", "Senior Manager, Revenue Strategy"]
