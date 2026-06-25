from __future__ import annotations

from src.enrichment.lifecycle import (
    LifecycleObservation,
    apply_lifecycle_observation,
    lifecycle_health_metrics,
    next_retry_at,
    preview_lifecycle_checks,
    retry_delay_days,
    run_lifecycle_checks,
    schedule_enrichment_retry,
)
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"


def job(**overrides) -> JobPosting:
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://careers.topgolf.com/jobs/1",
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


def observation(checked_at: str = NOW, **overrides) -> LifecycleObservation:
    values = {
        "checked_at": checked_at,
        "source_type": "company_ats",
        "source_url": "https://careers.topgolf.com/jobs/1",
        "authoritative": True,
    }
    values.update(overrides)
    return LifecycleObservation(**values)


def test_temporary_failure_does_not_close_job():
    posting = job()
    decision = apply_lifecycle_observation(
        posting,
        observation(http_status=503, error_type="http_retryable", message="temporary outage"),
    )
    assert posting.status == "open"
    assert posting.lifecycle_miss_count == 0
    assert decision.evidence_type == "temporary_failure"


def test_repeated_authoritative_404_closes_only_after_confirmation():
    posting = job()
    first = apply_lifecycle_observation(posting, observation(http_status=404, listed=False))
    assert first.status == "likely_closed"
    assert posting.lifecycle_miss_count == 1

    second = apply_lifecycle_observation(
        posting,
        observation(checked_at="2026-07-02T18:00:00Z", http_status=404, listed=False),
    )
    assert second.status == "confirmed_closed"
    assert posting.closed_date == "2026-07-02"
    assert posting.lifecycle_miss_count == 2


def test_expired_valid_through_marks_expired():
    posting = job()
    decision = apply_lifecycle_observation(
        posting,
        observation(http_status=200, listed=True, valid_through="2026-06-24"),
    )
    assert decision.status == "expired"
    assert posting.closed_date == "2026-06-24"
    assert decision.evidence_type == "valid_through_expired"


def test_removed_ats_posting_moves_to_likely_closed_then_closed():
    posting = job(source_primary="static_company")
    apply_lifecycle_observation(posting, observation(listed=False, http_status=200))
    assert posting.status == "likely_closed"
    apply_lifecycle_observation(
        posting,
        observation(checked_at="2026-07-02T18:00:00Z", listed=False, http_status=200),
    )
    assert posting.status == "confirmed_closed"


def test_gmail_only_unresolved_role_uses_separate_supporting_counter():
    posting = job(first_seen_date="2026-04-01")
    for checked_at in [
        "2026-06-25T18:00:00Z",
        "2026-07-02T18:00:00Z",
    ]:
        apply_lifecycle_observation(
            posting,
            observation(
                checked_at=checked_at,
                source_type="external_search",
                source_url="https://search.example.com/query",
                authoritative=False,
                supporting_absence=True,
                listed=None,
            ),
        )
    assert posting.status == "open"
    assert posting.missed_count == 2
    assert posting.lifecycle_miss_count == 0

    apply_lifecycle_observation(
        posting,
        observation(
            checked_at="2026-07-09T18:00:00Z",
            source_type="external_search",
            source_url="https://search.example.com/query",
            authoritative=False,
            supporting_absence=True,
            listed=None,
        ),
    )
    assert posting.status == "likely_closed"
    assert posting.missed_count == 3
    assert posting.lifecycle_miss_count == 0


def test_weak_misses_do_not_satisfy_authoritative_closure_threshold():
    posting = job(first_seen_date="2026-04-01")
    for checked_at in ["2026-06-25T18:00:00Z", "2026-07-02T18:00:00Z"]:
        apply_lifecycle_observation(
            posting,
            observation(
                checked_at=checked_at,
                source_type="external_search",
                source_url="https://search.example.com/query",
                authoritative=False,
                supporting_absence=True,
            ),
        )
    apply_lifecycle_observation(
        posting,
        observation(checked_at="2026-07-09T18:00:00Z", http_status=404, listed=False),
    )
    assert posting.status == "likely_closed"
    assert posting.lifecycle_miss_count == 1
    assert posting.missed_count == 0


def test_closed_job_reopens_and_resets_enrichment_when_authoritative_posting_is_rediscovered():
    posting = job(
        status="confirmed_closed",
        closed_date="2026-06-20",
        lifecycle_miss_count=2,
        enrichment_status="closed",
        enrichment_completed_at="2026-06-10T18:00:00Z",
    )
    decision = apply_lifecycle_observation(posting, observation(http_status=200, listed=True))
    assert decision.status == "reopened"
    assert posting.closed_date == ""
    assert posting.lifecycle_miss_count == 0
    assert posting.enrichment_status == "pending"
    assert posting.enrichment_completed_at == ""


def test_repeated_identical_lifecycle_run_is_idempotent():
    posting = job()
    checked = observation(http_status=404, listed=False)
    first = apply_lifecycle_observation(posting, checked)
    check_count = posting.lifecycle_check_count
    miss_count = posting.lifecycle_miss_count
    second = apply_lifecycle_observation(posting, checked)
    assert first.changed is True
    assert second.changed is False
    assert posting.lifecycle_check_count == check_count
    assert posting.lifecycle_miss_count == miss_count
    assert posting.status == "likely_closed"


def test_retry_schedule_declines_to_weekly():
    assert [retry_delay_days(value) for value in range(5)] == [0, 1, 3, 7, 7]
    assert next_retry_at(1, NOW) == "2026-06-26T18:00:00Z"
    assert next_retry_at(2, NOW) == "2026-06-28T18:00:00Z"
    assert next_retry_at(3, NOW) == "2026-07-02T18:00:00Z"


def test_only_transient_failure_rows_are_normalized():
    handoff = EnrichmentQueueItem(
        enrichment_id="enr-handoff",
        job_key="job-1",
        priority="high",
        status="not_found",
        attempt_count=6,
    )
    assert schedule_enrichment_retry(handoff, now=NOW) is False
    assert handoff.status == "not_found"
    assert handoff.next_attempt_at == ""

    transient = EnrichmentQueueItem(
        enrichment_id="enr-transient",
        job_key="job-1",
        priority="high",
        status="retryable_failure",
        attempt_count=6,
        last_attempted_at=NOW,
    )
    assert schedule_enrichment_retry(transient, now=NOW) is True
    assert transient.status == "retryable_failure"
    assert transient.next_attempt_at == "2026-07-02T18:00:00Z"

    transient.attempt_count = 8
    assert schedule_enrichment_retry(transient, now="2026-07-02T18:00:00Z") is True
    assert transient.status == "permanent_failure"
    assert transient.next_attempt_at == ""


def test_lifecycle_health_metrics_cover_required_populations():
    jobs = [
        job(job_key="verified", score_status="verified", verified_total_score=82, status="open"),
        job(job_key="provisional", status="open"),
        job(job_key="likely", status="likely_closed"),
        job(job_key="closed", status="confirmed_closed", closed_date="2026-06-20"),
        job(job_key="expired", status="expired", closed_date="2026-06-20"),
    ]
    queue = [
        EnrichmentQueueItem(enrichment_id="1", job_key="provisional", status="pending", attempt_count=0, created_at="2026-06-20T18:00:00Z"),
        EnrichmentQueueItem(enrichment_id="2", job_key="likely", status="retryable_failure", attempt_count=2, created_at="2026-06-22T18:00:00Z"),
        EnrichmentQueueItem(enrichment_id="3", job_key="other", status="ambiguous", attempt_count=1, created_at="2026-06-23T18:00:00Z"),
        EnrichmentQueueItem(enrichment_id="4", job_key="verified", status="enriched", attempt_count=1, created_at="2026-06-21T18:00:00Z"),
    ]
    metrics = lifecycle_health_metrics(jobs, queue, now=NOW)
    assert metrics["open_verified_jobs"] == 1
    assert metrics["open_provisional_jobs"] == 1
    assert metrics["enrichment_backlog"] == 2
    assert metrics["retryable_failures"] == 1
    assert metrics["ambiguous_matches"] == 1
    assert metrics["jobs_likely_closed"] == 1
    assert metrics["jobs_confirmed_closed"] == 2
    assert metrics["oldest_pending_enrichment_days"] == 5
    assert metrics["average_enrichment_attempts"] == 1.33
    assert metrics["enrichment_success_rate_percent"] == 33.3


class FakeSheetClient:
    def __init__(self, jobs, queue):
        self.tables = {
            "Jobs": [posting.to_dict() for posting in jobs],
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

    def update_job(self, row_number, posting):
        self.tables["Jobs"][row_number - 2] = posting.to_dict()

    def append_run(self, record):
        self.tables["Runs"].append(dict(record))


def test_workbook_run_records_closure_evidence_and_closes_queue():
    posting = job()
    queue_item = EnrichmentQueueItem(
        enrichment_id="enr-1",
        job_key=posting.job_key,
        company=posting.company,
        title=posting.title,
        lead_url=posting.canonical_url,
        priority="high",
        status="not_found",
        attempt_count=4,
    )
    client = FakeSheetClient([posting], [queue_item])

    def checker(_posting, *, checked_at):
        return observation(
            checked_at=checked_at,
            explicitly_closed=True,
            listed=False,
            http_status=200,
        )

    summary = run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=True)
    assert summary.confirmed_closed == 1
    assert summary.evidence_written == 1
    assert client.tables["Jobs"][0]["status"] == "confirmed_closed"
    assert client.tables["Enrichment_Queue"][0]["status"] == "closed"
    assert client.tables["Enrichment_Evidence"][0]["source_type"] == "lifecycle_company_ats"
    assert len(client.tables["Runs"]) == 1


def test_reopen_activates_only_current_canonical_queue_row():
    posting = job(status="confirmed_closed", closed_date="2026-06-20", enrichment_status="closed")
    current_id = enrichment_id_for(posting.job_key, posting.canonical_url)
    obsolete_url = "https://www.linkedin.com/jobs/view/1"
    obsolete_id = enrichment_id_for(posting.job_key, obsolete_url)
    queue = [
        EnrichmentQueueItem(
            enrichment_id=obsolete_id,
            job_key=posting.job_key,
            lead_url=obsolete_url,
            status="closed",
            attempt_count=5,
        ),
        EnrichmentQueueItem(
            enrichment_id=current_id,
            job_key=posting.job_key,
            lead_url=posting.canonical_url,
            status="closed",
            attempt_count=6,
        ),
    ]
    client = FakeSheetClient([posting], queue)

    def checker(_posting, *, checked_at):
        return observation(checked_at=checked_at, listed=True, http_status=200)

    run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)
    assert client.tables["Jobs"][0]["status"] == "reopened"
    assert client.tables["Jobs"][0]["enrichment_status"] == "pending"
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    assert rows[current_id]["status"] == "pending"
    assert rows[current_id]["attempt_count"] == 0
    assert rows[obsolete_id]["status"] == "closed"
    assert rows[obsolete_id]["attempt_count"] == 5


def test_job_key_scope_does_not_mutate_unrelated_retries_and_dry_run_previews_changes():
    target = job(job_key="target", lifecycle_next_check_at="2026-07-01T18:00:00Z")
    other = job(job_key="other", lifecycle_next_check_at="2026-07-01T18:00:00Z")
    queue = [
        EnrichmentQueueItem(
            enrichment_id="target-q",
            job_key="target",
            status="retryable_failure",
            priority="high",
            attempt_count=1,
            last_attempted_at=NOW,
            next_attempt_at="2026-06-25T19:00:00Z",
        ),
        EnrichmentQueueItem(
            enrichment_id="other-q",
            job_key="other",
            status="retryable_failure",
            priority="high",
            attempt_count=1,
            last_attempted_at=NOW,
            next_attempt_at="2026-06-25T19:00:00Z",
        ),
    ]
    client = FakeSheetClient([target, other], queue)
    preview = preview_lifecycle_checks(client, now=NOW, job_key="target")
    assert [row["job_key"] for row in preview["retry_updates"]] == ["target"]

    run_lifecycle_checks(client, now=NOW, job_key="target", write_run_record=False)
    assert client.tables["Enrichment_Queue"][0]["next_attempt_at"] == "2026-06-26T18:00:00Z"
    assert client.tables["Enrichment_Queue"][1]["next_attempt_at"] == "2026-06-25T19:00:00Z"


def test_topgolf_and_toyota_remain_visible_after_temporary_lifecycle_failures():
    jobs = [
        job(job_key="topgolf", company="Topgolf", title="Sr Manager, Strategic Planning"),
        job(job_key="toyota", company="Toyota North America", title="National Manager, Product"),
    ]
    for posting in jobs:
        apply_lifecycle_observation(
            posting,
            observation(
                source_url=posting.canonical_url,
                http_status=503,
                error_type="http_retryable",
            ),
        )
        assert posting.status == "open"
        assert posting.potential_priority == "high"
        assert posting.score_status == "provisional"
