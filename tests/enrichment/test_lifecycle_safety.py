import json

from src.enrichment.fetcher import FetchResult
from src.enrichment.lifecycle import (
    DirectUrlLifecycleChecker,
    LifecycleObservation,
    apply_lifecycle_observation,
    is_authoritative_lifecycle_url,
)
from src.models import JobPosting


def _job(**overrides) -> JobPosting:
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://www.linkedin.com/jobs/view/123",
        "source_job_id": "123",
        "source_primary": "gmail_alert",
        "status": "open",
        "potential_priority": "high",
        "score_status": "provisional",
    }
    values.update(overrides)
    return JobPosting(**values)


def _posting_html(
    *,
    title: str = "Sr Manager, Strategic Planning",
    company: str = "Topgolf",
    location: str = "Dallas",
    body_text: str = "Apply now",
    extra_script: str = "",
) -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": location,
                "addressRegion": "TX",
                "addressCountry": "US",
            },
        },
        "description": (
            "Lead strategic planning, growth initiatives, executive analysis, and cross-functional execution. "
            "Manage a team and partner with senior leaders across the business."
        ),
        "url": "https://careers.topgolf.com/jobs/123",
    }
    return (
        "<html><head><script type='application/ld+json'>"
        f"{json.dumps(posting)}"
        "</script></head><body>"
        f"<main>{body_text}</main><script>{extra_script}</script>"
        "</body></html>"
    )


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


def test_different_authoritative_urls_on_same_day_count_as_one_miss():
    job = _job()
    apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T10:00:00Z",
            source_type="company_ats",
            source_url="https://careers.topgolf.com/jobs/123",
            authoritative=True,
            http_status=404,
            listed=False,
        ),
    )
    second = apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T11:00:00Z",
            source_type="company_inventory",
            source_url="https://careers.topgolf.com/search?job=123",
            authoritative=True,
            listed=False,
        ),
    )

    assert second.changed is True
    assert job.status == "likely_closed"
    assert job.lifecycle_miss_count == 1
    assert job.lifecycle_last_authoritative_miss_date == "2026-06-25"

    apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-07-02T10:00:00Z",
            source_type="company_ats",
            source_url="https://careers.topgolf.com/jobs/123",
            authoritative=True,
            http_status=404,
            listed=False,
        ),
    )
    assert job.status == "confirmed_closed"
    assert job.lifecycle_miss_count == 2
    assert job.lifecycle_last_authoritative_miss_date == "2026-07-02"


def test_authority_requires_employer_or_verified_ats_evidence_and_rejects_spoofed_hosts():
    job = _job()
    assert is_authoritative_lifecycle_url(job.canonical_url, job) is False
    assert is_authoritative_lifecycle_url("https://boards.greenhouse.io/topgolf/jobs/123", job) is True
    assert is_authoritative_lifecycle_url("https://careers.topgolf.com/jobs/123", job) is True
    assert is_authoritative_lifecycle_url("https://greenhouse.io.example.com/topgolf/jobs/123", job) is False
    assert is_authoritative_lifecycle_url("https://not-topgolf.example.com/careers/123", job) is False

    verified = _job(
        enrichment_source_url="https://careers.example.com/jobs/123",
        enrichment_status="enriched",
        enrichment_match_confidence=90,
    )
    assert is_authoritative_lifecycle_url(verified.enrichment_source_url, verified) is True


class StaticFetcher:
    def __init__(self, result):
        self.result = result

    def fetch(self, _url):
        return self.result


def test_unparseable_http_200_page_is_unresolved_not_authoritative_absence():
    job = _job(canonical_url="https://careers.topgolf.com/jobs/123")
    checker = DirectUrlLifecycleChecker(
        StaticFetcher(
            FetchResult(
                requested_url=job.canonical_url,
                final_url=job.canonical_url,
                status_code=200,
                content_type="text/html",
                text="<html><body>JavaScript required</body></html>",
            )
        )
    )
    observation = checker(job, checked_at="2026-06-25T10:00:00Z")
    assert observation.authoritative is True
    assert observation.listed is None
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
    assert job.lifecycle_miss_count == 0


def test_mismatched_authoritative_posting_cannot_open_or_reopen_job():
    job = _job(
        canonical_url="https://careers.topgolf.com/jobs/123",
        status="confirmed_closed",
        closed_date="2026-06-20",
        enrichment_status="closed",
    )
    checker = DirectUrlLifecycleChecker(
        StaticFetcher(
            FetchResult(
                requested_url=job.canonical_url,
                final_url=job.canonical_url,
                status_code=200,
                content_type="text/html",
                text=_posting_html(title="Staff Accountant", location="Austin"),
            )
        )
    )

    observation = checker(job, checked_at="2026-06-25T10:00:00Z")
    assert observation.authoritative is True
    assert observation.listed is None
    assert observation.explicitly_closed is False
    assert "did not match" in observation.message

    apply_lifecycle_observation(job, observation)
    assert job.status == "confirmed_closed"
    assert job.closed_date == "2026-06-20"
    assert job.enrichment_status == "closed"


def test_closure_phrase_inside_script_does_not_close_matching_posting():
    job = _job(canonical_url="https://careers.topgolf.com/jobs/123")
    checker = DirectUrlLifecycleChecker(
        StaticFetcher(
            FetchResult(
                requested_url=job.canonical_url,
                final_url=job.canonical_url,
                status_code=200,
                content_type="text/html",
                text=_posting_html(extra_script="const closedLabel = 'job is no longer available';"),
            )
        )
    )

    observation = checker(job, checked_at="2026-06-25T10:00:00Z")
    assert observation.listed is True
    assert observation.explicitly_closed is False
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
    assert job.lifecycle_miss_count == 0


def test_visible_closure_text_conflicting_with_matching_posting_is_unresolved():
    job = _job(canonical_url="https://careers.topgolf.com/jobs/123")
    checker = DirectUrlLifecycleChecker(
        StaticFetcher(
            FetchResult(
                requested_url=job.canonical_url,
                final_url=job.canonical_url,
                status_code=200,
                content_type="text/html",
                text=_posting_html(body_text="This job is no longer available"),
            )
        )
    )

    observation = checker(job, checked_at="2026-06-25T10:00:00Z")
    assert observation.listed is None
    assert observation.explicitly_closed is False
    assert "conflicts" in observation.message
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
    assert job.lifecycle_miss_count == 0


def test_final_redirect_is_revalidated_before_authoritative_decision():
    job = _job(canonical_url="https://careers.topgolf.com/jobs/123")
    checker = DirectUrlLifecycleChecker(
        StaticFetcher(
            FetchResult(
                requested_url=job.canonical_url,
                final_url="https://example.com/jobs/123",
                status_code=200,
                content_type="text/html",
                text="This job is no longer available",
            )
        )
    )
    observation = checker(job, checked_at="2026-06-25T10:00:00Z")
    assert observation.authoritative is False
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
