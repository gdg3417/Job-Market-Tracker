from __future__ import annotations

import json

from src.enrichment.fetcher import EnrichmentFetchError, FetchResult
from src.enrichment.lifecycle import (
    DirectUrlLifecycleChecker,
    LifecycleObservation,
    apply_lifecycle_observation,
    lifecycle_url_for_job,
    run_lifecycle_checks,
)
from src.enrichment.models import EnrichmentQueueItem
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"


def _job(**overrides) -> JobPosting:
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://careers.topgolf.com/jobs/123",
        "source_job_id": "123",
        "source_primary": "gmail_alert",
        "first_seen_date": "2026-06-01",
        "last_seen_date": "2026-06-20",
        "status": "open",
        "potential_priority": "high",
        "potential_priority_score": 90,
        "score_status": "provisional",
        "enrichment_status": "not_found",
        "enrichment_priority": "high",
    }
    values.update(overrides)
    return JobPosting(**values)


def _posting_html(
    *,
    title: str = "Sr Manager, Strategic Planning",
    company: str = "Topgolf",
    location: str = "Dallas",
    valid_through: str = "",
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
    if valid_through:
        posting["validThrough"] = valid_through
    return (
        "<html><head><script type='application/ld+json'>"
        f"{json.dumps(posting)}"
        "</script></head><body><main>Apply now</main></body></html>"
    )


class RecordingFetcher:
    def __init__(self, result: FetchResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.requested_urls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requested_urls.append(url)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeSheetClient:
    def __init__(self, jobs: list[JobPosting], queue: list[EnrichmentQueueItem]):
        self.tables = {
            "Jobs": [job.to_dict() for job in jobs],
            "Enrichment_Queue": [item.to_dict() for item in queue],
            "Enrichment_Evidence": [],
            "Runs": [],
        }

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def read_jobs_with_row_numbers(self):
        return [(index + 2, JobPosting.from_dict(row)) for index, row in enumerate(self.tables["Jobs"])]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()


def test_mismatched_expired_posting_cannot_expire_tracked_job():
    job = _job()
    fetcher = RecordingFetcher(
        FetchResult(
            requested_url=job.canonical_url,
            final_url=job.canonical_url,
            status_code=200,
            content_type="text/html",
            text=_posting_html(title="Staff Accountant", location="Austin", valid_through="2026-06-20"),
        )
    )
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)

    assert observation.authoritative is True
    assert observation.listed is None
    assert observation.valid_through == ""
    assert "did not match" in observation.message

    apply_lifecycle_observation(job, observation)
    assert job.status == "open"
    assert job.closed_date == ""
    assert job.lifecycle_miss_count == 0


def test_matching_expired_posting_can_still_expire_tracked_job():
    job = _job()
    fetcher = RecordingFetcher(
        FetchResult(
            requested_url=job.canonical_url,
            final_url=job.canonical_url,
            status_code=200,
            content_type="text/html",
            text=_posting_html(valid_through="2026-06-20"),
        )
    )
    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)

    assert observation.listed is True
    assert observation.valid_through == "2026-06-20"
    apply_lifecycle_observation(job, observation)
    assert job.status == "expired"
    assert job.closed_date == "2026-06-20"


def test_unverified_enrichment_source_is_not_selected_for_lifecycle_check():
    job = _job(
        enrichment_source_url="https://boards.greenhouse.io/topgolf/jobs/999",
        enrichment_match_confidence=25,
        enrichment_status="not_found",
    )
    fetcher = RecordingFetcher(
        FetchResult(
            requested_url=job.canonical_url,
            final_url=job.canonical_url,
            status_code=200,
            content_type="text/html",
            text=_posting_html(),
        )
    )

    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)

    assert lifecycle_url_for_job(job) == job.canonical_url
    assert fetcher.requested_urls == [job.canonical_url]
    assert observation.authoritative is True
    assert observation.listed is True


def test_verified_enrichment_source_remains_trusted_while_reopened_job_is_pending():
    verified_url = "https://careers.topgolf.com/jobs/123"
    job = _job(
        canonical_url="https://www.linkedin.com/jobs/view/123",
        enrichment_source_url=verified_url,
        enrichment_match_confidence=95,
        enrichment_status="pending",
        status="reopened",
    )
    fetcher = RecordingFetcher(
        FetchResult(
            requested_url=verified_url,
            final_url=verified_url,
            status_code=200,
            content_type="text/html",
            text=_posting_html(),
        )
    )

    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)

    assert lifecycle_url_for_job(job) == verified_url
    assert fetcher.requested_urls == [verified_url]
    assert observation.authoritative is True
    assert observation.listed is True


def test_untrusted_job_board_redirect_to_ats_404_cannot_create_closure_evidence():
    job = _job(canonical_url="https://www.linkedin.com/jobs/view/123")
    fetcher = RecordingFetcher(
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


def test_untrusted_job_board_redirect_can_become_authoritative_after_a_valid_match():
    job = _job(canonical_url="https://www.linkedin.com/jobs/view/123")
    final_url = "https://careers.topgolf.com/jobs/123"
    fetcher = RecordingFetcher(
        FetchResult(
            requested_url=job.canonical_url,
            final_url=final_url,
            status_code=200,
            content_type="text/html",
            text=_posting_html(),
        )
    )

    observation = DirectUrlLifecycleChecker(fetcher)(job, checked_at=NOW)

    assert observation.authoritative is True
    assert observation.listed is True
    apply_lifecycle_observation(job, observation)
    assert job.status == "open"


def test_older_authoritative_miss_does_not_advance_counter_or_move_date_backward():
    job = _job(
        status="likely_closed",
        lifecycle_miss_count=1,
        lifecycle_last_authoritative_miss_date="2026-07-02",
    )
    decision = apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T18:00:00Z",
            source_type="company_ats",
            source_url=job.canonical_url,
            authoritative=True,
            http_status=404,
            listed=False,
        ),
    )

    assert decision.changed is True
    assert job.status == "likely_closed"
    assert job.lifecycle_miss_count == 1
    assert job.lifecycle_last_authoritative_miss_date == "2026-07-02"
    assert job.closed_date == ""
    assert "Older authoritative absence" in job.lifecycle_reason


def test_stale_observation_cannot_reverse_newer_lifecycle_state():
    job = _job(
        status="reopened",
        enrichment_status="pending",
        lifecycle_last_checked_at="2026-07-02T18:00:00Z",
        lifecycle_next_check_at="2026-07-09T18:00:00Z",
        lifecycle_last_evidence_key="newer-evidence",
    )
    decision = apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at="2026-06-25T18:00:00Z",
            source_type="company_ats",
            source_url=job.canonical_url,
            authoritative=True,
            explicitly_closed=True,
        ),
    )

    assert decision.changed is False
    assert job.status == "reopened"
    assert job.lifecycle_last_checked_at == "2026-07-02T18:00:00Z"
    assert job.lifecycle_next_check_at == "2026-07-09T18:00:00Z"
    assert job.lifecycle_last_evidence_key == "newer-evidence"
    assert job.lifecycle_miss_count == 0


def test_reopened_queue_rows_receive_a_fresh_retry_budget_and_clean_state():
    job = _job(
        status="confirmed_closed",
        closed_date="2026-06-20",
        enrichment_status="closed",
        enrichment_source_url="https://careers.topgolf.com/jobs/123",
        enrichment_match_confidence=95,
    )
    queue = [
        EnrichmentQueueItem(
            enrichment_id="enr-1",
            job_key=job.job_key,
            company=job.company,
            title=job.title,
            lead_url=job.canonical_url,
            status="closed",
            current_stage="external_search",
            attempt_count=8,
            next_attempt_at="2026-07-01T18:00:00Z",
            last_attempted_at="2026-06-20T18:00:00Z",
            matched_url="https://wrong.example/jobs/1",
            match_confidence=22,
            fields_recovered="title, location",
            error_type="posting_closed",
            error_message="old state",
            created_at="2026-06-01T18:00:00Z",
            updated_at="2026-06-20T18:00:00Z",
        )
    ]
    client = FakeSheetClient([job], queue)

    def checker(_job, *, checked_at):
        return LifecycleObservation(
            checked_at=checked_at,
            source_type="direct_url",
            source_url="https://careers.topgolf.com/jobs/123",
            authoritative=True,
            http_status=200,
            listed=True,
        )

    run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)
    row = client.tables["Enrichment_Queue"][0]

    assert row["status"] == "pending"
    assert row["current_stage"] == "direct_url"
    assert row["attempt_count"] == 0
    assert row["next_attempt_at"] == ""
    assert row["last_attempted_at"] == ""
    assert row["matched_url"] == ""
    assert row["match_confidence"] == ""
    assert row["fields_recovered"] == ""
    assert row["error_type"] == ""
    assert row["error_message"] == ""
    assert row["created_at"] == NOW
    assert row["updated_at"] == NOW


def test_stale_run_writes_audit_evidence_without_mutating_job_or_queue():
    job = _job(
        status="reopened",
        enrichment_status="pending",
        lifecycle_last_checked_at="2026-07-02T18:00:00Z",
        lifecycle_next_check_at="",
    )
    queue = [EnrichmentQueueItem(enrichment_id="enr-1", job_key=job.job_key, status="pending")]
    client = FakeSheetClient([job], queue)

    def checker(_job, *, checked_at):
        return LifecycleObservation(
            checked_at=checked_at,
            source_type="company_ats",
            source_url="https://careers.topgolf.com/jobs/123",
            authoritative=True,
            http_status=404,
            listed=False,
        )

    summary = run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)

    assert summary.jobs_updated == 0
    assert summary.jobs_unchanged == 1
    assert summary.evidence_written == 1
    assert client.tables["Jobs"][0]["status"] == "reopened"
    assert client.tables["Jobs"][0]["lifecycle_last_checked_at"] == "2026-07-02T18:00:00Z"
    assert client.tables["Enrichment_Queue"][0]["status"] == "pending"
