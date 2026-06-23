from src.data_quality import filter_jobs_for_upsert
from src.sources.gmail_alerts import (
    GmailAlertEmail,
    parse_job_alert_email,
    parsed_alerts_to_jobs,
    should_upsert_alert,
)


def test_production_plain_text_view_job_blocks_keep_preceding_card_identity():
    email = GmailAlertEmail(
        message_id="19eeb7f9ad20df2f",
        thread_id="19eeb7f9ad20df2f",
        subject="National Manager, Product at Toyota North America",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Sun, 21 Jun 2026 18:44:26 +0000",
        body_text="""
        Your job alert for product line manager in Dallas
        New jobs match your preferences.

        National Manager, Product
        Toyota North America
        Plano, TX

        186 school alumni
        View job: https://www.linkedin.com/comm/jobs/view/4430066274/?trackingId=toyota-card

        ---------------------------------------------------------

        Head of Product
        Flooret
        Grapevine, TX
        View job: https://www.linkedin.com/comm/jobs/view/4430017649/?trackingId=flooret-card
        """,
    )

    alerts = parse_job_alert_email(email)
    by_id = {
        alert.source_job_id.removeprefix("linkedin-"): alert
        for alert in alerts
    }

    assert (by_id["4430066274"].title, by_id["4430066274"].company, by_id["4430066274"].location) == (
        "National Manager, Product",
        "Toyota North America",
        "Plano, TX",
    )
    assert (by_id["4430017649"].title, by_id["4430017649"].company, by_id["4430017649"].location) == (
        "Head of Product",
        "Flooret",
        "Grapevine, TX",
    )
    assert by_id["4430066274"].url == "https://www.linkedin.com/jobs/view/4430066274"
    assert by_id["4430017649"].url == "https://www.linkedin.com/jobs/view/4430017649"
    assert should_upsert_alert(by_id["4430066274"])

    candidate_jobs = parsed_alerts_to_jobs(alerts, seen_date="2026-06-23")
    accepted_jobs, rejected_jobs = filter_jobs_for_upsert(candidate_jobs)
    assert any(job.canonical_url == by_id["4430066274"].url for job in accepted_jobs)
    assert all(rejection.job.canonical_url != by_id["4430066274"].url for rejection in rejected_jobs)


def test_plain_text_urls_before_card_text_keep_following_segment_identity():
    email = GmailAlertEmail(
        message_id="url-before-card-text",
        subject="LinkedIn jobs digest",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Sun, 21 Jun 2026 18:44:26 +0000",
        body_text="""
        https://www.linkedin.com/comm/jobs/view/7000000101/?trackingId=first
        Director, Product Strategy
        Acme Corp
        Dallas, TX

        https://www.linkedin.com/comm/jobs/view/7000000102/?trackingId=second
        Senior Manager, Revenue Strategy
        Beta Co
        Plano, TX
        """,
    )

    by_id = {
        alert.source_job_id.removeprefix("linkedin-"): alert
        for alert in parse_job_alert_email(email)
    }

    assert (by_id["7000000101"].title, by_id["7000000101"].company, by_id["7000000101"].location) == (
        "Director, Product Strategy",
        "Acme Corp",
        "Dallas, TX",
    )
    assert (by_id["7000000102"].title, by_id["7000000102"].company, by_id["7000000102"].location) == (
        "Senior Manager, Revenue Strategy",
        "Beta Co",
        "Plano, TX",
    )


def test_production_shaped_toyota_lead_card_keeps_job_identity():
    email = GmailAlertEmail(
        message_id="19eeb7f9ad20df2f",
        thread_id="19eeb7f9ad20df2f",
        subject="National Manager, Product at Toyota North America",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Sun, 21 Jun 2026 18:44:26 +0000",
        body_text="""
        &#91;Your job alert for product line manager&#93;&#40;https://www.linkedin.com/comm/jobs/search?keywords=product+line+manager&originToLandingJobPostings=4430066274,4430017649&#41;

        New jobs in Dallas match your preferences.

        &#91;Toyota North America&#93;&#40;https://www.linkedin.com/comm/jobs/view/4430066274/?trackingId=company-logo&#41;
        National Manager, Product
        Toyota North America · Plano, TX &#40;Hybrid&#41;

        &#91;Flooret&#93;&#40;https://www.linkedin.com/comm/jobs/view/4430017649/?trackingId=company-logo-two&#41;
        &#91;Director, Product Strategy
        Flooret · Grapevine, TX &#40;Hybrid&#41;&#93;&#40;https://www.linkedin.com/comm/jobs/view/4430017649/?trackingId=job-card-two&#41;
        """,
    )

    alerts = parse_job_alert_email(email)
    accepted_by_id = {
        alert.source_job_id.removeprefix("linkedin-"): alert
        for alert in alerts
        if should_upsert_alert(alert)
    }

    assert "4430066274" in accepted_by_id
    toyota = accepted_by_id["4430066274"]
    assert (toyota.title, toyota.company, toyota.location) == (
        "National Manager, Product",
        "Toyota North America",
        "Plano, TX",
    )
    assert toyota.url == "https://www.linkedin.com/jobs/view/4430066274"
    assert "Your job alert" not in toyota.title
    assert "New jobs match" not in toyota.company

    candidate_jobs = parsed_alerts_to_jobs(alerts, seen_date="2026-06-23")
    accepted_jobs, rejected_jobs = filter_jobs_for_upsert(candidate_jobs)
    assert any(job.canonical_url == toyota.url for job in accepted_jobs)
    assert all(rejection.job.canonical_url != toyota.url for rejection in rejected_jobs)


def test_valid_direct_card_is_not_overridden_by_neighboring_metadata():
    email = GmailAlertEmail(
        message_id="valid-lead-card",
        subject="Unrelated digest subject",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Sun, 21 Jun 2026 18:44:26 +0000",
        body_text="""
        &#91;Your job alert for product manager&#93;&#40;https://www.linkedin.com/comm/jobs/search?keywords=product+manager&#41;
        New jobs in Dallas match your preferences.

        &#91;Acme Corp&#93;&#40;https://www.linkedin.com/comm/jobs/view/7000000001/?trackingId=logo&#41;
        &#91;Director, Product Strategy
        Acme Corp · Dallas, TX &#40;Hybrid&#41;&#93;&#40;https://www.linkedin.com/comm/jobs/view/7000000001/?trackingId=card&#41;

        &#91;Second Corp&#93;&#40;https://www.linkedin.com/comm/jobs/view/7000000002/?trackingId=logo-two&#41;
        &#91;Senior Manager, Business Operations
        Second Corp · Plano, TX &#40;On-site&#41;&#93;&#40;https://www.linkedin.com/comm/jobs/view/7000000002/?trackingId=card-two&#41;
        """,
    )

    accepted = {
        alert.source_job_id: alert
        for alert in parse_job_alert_email(email)
        if should_upsert_alert(alert)
    }

    assert accepted["linkedin-7000000001"].title == "Director, Product Strategy"
    assert accepted["linkedin-7000000001"].company == "Acme Corp"
    assert accepted["linkedin-7000000001"].location == "Dallas, TX"
