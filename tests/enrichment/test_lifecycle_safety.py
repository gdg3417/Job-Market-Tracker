from src.enrichment.lifecycle import LifecycleObservation, apply_lifecycle_observation, is_authoritative_lifecycle_url
from src.models import JobPosting


def _job(**overrides) -> JobPosting:
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://www.linkedin.com/jobs/view/123",
        "source_primary": "gmail_alert",
        "status": "open",
        "potential_priority": "high",
        "score_status": "provisional",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_non_authoritative_closed_text_does_not_close_job():
    job = _job()
    decision = apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T10:00:00Z",
            source_type="job_board",
            source_url=job.canonical_url,
            authoritative=False,
            explicitly_closed=True,
            supporting_absence=True,
        ),
    )
    assert job.status == "open"
    assert decision.evidence_type == "supporting_absence"


def test_non_authoritative_listing_does_not_reopen_closed_job():
    job = _job(status="confirmed_closed", closed_date="2026-06-20")
    apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T10:00:00Z",
            source_type="job_board",
            source_url=job.canonical_url,
            authoritative=False,
            listed=True,
        ),
    )
    assert job.status == "confirmed_closed"
    assert job.closed_date == "2026-06-20"


def test_same_day_duplicate_absence_does_not_confirm_closure():
    job = _job()
    first = LifecycleObservation(
        checked_at="2026-06-25T10:00:00Z",
        source_type="company_ats",
        source_url="https://careers.topgolf.com/jobs/123",
        authoritative=True,
        http_status=404,
        listed=False,
    )
    second = LifecycleObservation(
        checked_at="2026-06-25T11:00:00Z",
        source_type="company_ats",
        source_url="https://careers.topgolf.com/jobs/123",
        authoritative=True,
        http_status=404,
        listed=False,
    )
    apply_lifecycle_observation(job, first)
    duplicate = apply_lifecycle_observation(job, second)
    assert job.status == "likely_closed"
    assert job.lifecycle_miss_count == 1
    assert duplicate.changed is False


def test_authority_requires_employer_or_verified_ats_evidence():
    job = _job()
    assert is_authoritative_lifecycle_url(job.canonical_url, job) is False
    assert is_authoritative_lifecycle_url("https://boards.greenhouse.io/topgolf/jobs/123", job) is True
    assert is_authoritative_lifecycle_url("https://careers.topgolf.com/jobs/123", job) is True

    verified = _job(
        enrichment_source_url="https://careers.example.com/jobs/123",
        enrichment_status="enriched",
        enrichment_match_confidence=90,
    )
    assert is_authoritative_lifecycle_url(verified.enrichment_source_url, verified) is True
