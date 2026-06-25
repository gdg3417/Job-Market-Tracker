import json

from src.enrichment.fetcher import FetchResult
from src.enrichment.lifecycle import DirectUrlLifecycleChecker, apply_lifecycle_observation, lifecycle_url_for_job
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"


def make_job(**overrides):
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://careers.topgolf.com/jobs/123",
        "source_job_id": "123",
        "status": "open",
        "potential_priority": "high",
        "score_status": "provisional",
        "enrichment_status": "not_found",
    }
    values.update(overrides)
    return JobPosting(**values)


def posting_html(title="Sr Manager, Strategic Planning", location="Dallas", valid_through=""):
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"@type": "Organization", "name": "Topgolf"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": location, "addressRegion": "TX"}},
        "description": "Lead strategic planning, growth initiatives, executive analysis, and cross-functional execution.",
        "url": "https://careers.topgolf.com/jobs/123",
    }
    if valid_through:
        posting["validThrough"] = valid_through
    return f"<html><head><script type='application/ld+json'>{json.dumps(posting)}</script></head><body>Apply now</body></html>"


class Fetcher:
    def __init__(self, result):
        self.result = result
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.result


def test_mismatched_expired_posting_does_not_expire_tracked_job():
    job = make_job()
    fetcher = Fetcher(FetchResult(job.canonical_url, job.canonical_url, 200, "text/html", posting_html("Staff Accountant", "Austin", "2026-06-20")))
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert observation.listed is None
    assert observation.valid_through == ""
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"


def test_matching_expired_posting_still_expires_tracked_job():
    job = make_job()
    fetcher = Fetcher(FetchResult(job.canonical_url, job.canonical_url, 200, "text/html", posting_html(valid_through="2026-06-20")))
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert observation.valid_through == "2026-06-20"
    apply_lifecycle_observation(job, observation)
    assert job.status == "expired"


def test_unverified_enrichment_source_is_not_selected():
    job = make_job(
        enrichment_source_url="https://boards.greenhouse.io/topgolf/jobs/999",
        enrichment_match_confidence=25,
        enrichment_status="not_found",
    )
    fetcher = Fetcher(FetchResult(job.canonical_url, job.canonical_url, 200, "text/html", posting_html()))
    DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert lifecycle_url_for_job(job) == job.canonical_url
    assert fetcher.urls == [job.canonical_url]
