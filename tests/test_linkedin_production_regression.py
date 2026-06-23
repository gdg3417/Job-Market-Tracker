from src.data_quality import filter_jobs_for_upsert
from src.sources.gmail_alerts import (
    GmailAlertEmail,
    parse_job_alert_email,
    parsed_alerts_to_jobs,
    should_upsert_alert,
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
        &#91;National Manager, Product Toyota North America · Plano, TX &#40;Hybrid&#41;&#93;&#40;https://www.linkedin.com/comm/jobs/view/4430066274/?trackingId=job-card&#41;

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
