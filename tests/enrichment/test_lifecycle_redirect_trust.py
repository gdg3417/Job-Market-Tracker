import json

from src.enrichment.fetcher import EnrichmentFetchError, FetchResult
from src.enrichment.lifecycle import DirectUrlLifecycleChecker, apply_lifecycle_observation, lifecycle_url_for_job
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"


def make_job(**overrides):
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://www.linkedin.com/jobs/view/123",
        "source_job_id": "123",
        "status": "open",
        "potential_priority": "high",
        "score_status": "provisional",
        "enrichment_status": "not_found",
    }
    values.update(overrides)
    return JobPosting(**values)


def posting_html():
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Sr Manager, Strategic Planning",
        "hiringOrganization": {"@type": "Organization", "name": "Topgolf"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Dallas", "addressRegion": "TX"}},
        "description": "Lead strategic planning, growth initiatives, executive analysis, and cross-functional execution.",
        "url": "https://careers.topgolf.com/jobs/123",
    }
    return f"<html><head><script type='application/ld+json'>{json.dumps(posting)}</script></head><body>Apply now</body></html>"


class Fetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.result


def test_verified_source_remains_trusted_while_reopened_job_is_pending():
    verified_url = "https://careers.topgolf.com/jobs/123"
    job = make_job(
        enrichment_source_url=verified_url,
        enrichment_match_confidence=95,
        enrichment_status="pending",
        status="reopened",
    )
    fetcher = Fetcher(FetchResult(verified_url, verified_url, 200, "text/html", posting_html()))
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert lifecycle_url_for_job(job) == verified_url
    assert fetcher.urls == [verified_url]
    assert observation.authoritative is True
    assert observation.listed is True


def test_untrusted_job_board_redirect_to_ats_404_is_not_closure_evidence():
    job = make_job()
    fetcher = Fetcher(
        error=EnrichmentFetchError(
            "not_found",
            "posting missing",
            retryable=False,
            status_code=404,
            final_url="https://boards.greenhouse.io/topgolf/jobs/123",
        )
    )
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert observation.authoritative is False
    assert observation.listed is None
    assert observation.supporting_absence is False
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
    assert job.lifecycle_miss_count == 0


def test_untrusted_redirect_becomes_authoritative_after_valid_match():
    job = make_job()
    final_url = "https://careers.topgolf.com/jobs/123"
    fetcher = Fetcher(FetchResult(job.canonical_url, final_url, 200, "text/html", posting_html()))
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)
    assert observation.authoritative is True
    assert observation.listed is True
