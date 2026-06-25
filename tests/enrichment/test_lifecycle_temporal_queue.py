from src.enrichment.lifecycle import LifecycleObservation, apply_lifecycle_observation, run_lifecycle_checks
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"


def make_job(**overrides):
    values = {
        "job_key": "job-1",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": "https://careers.topgolf.com/jobs/123",
        "source_primary": "gmail_alert",
        "first_seen_date": "2026-06-01",
        "status": "open",
        "potential_priority": "high",
        "potential_priority_score": 90,
        "score_status": "provisional",
        "enrichment_status": "not_found",
    }
    values.update(overrides)
    return JobPosting(**values)


class FakeSheetClient:
    def __init__(self, jobs, queue):
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


def missing_observation(checked_at):
    return LifecycleObservation(
        checked_at=checked_at,
        source_type="company_ats",
        source_url="https://careers.topgolf.com/jobs/123",
        authoritative=True,
        http_status=404,
        listed=False,
    )


def test_older_authoritative_miss_does_not_advance_counter_or_date():
    job = make_job(
        status="likely_closed",
        lifecycle_miss_count=1,
        lifecycle_last_authoritative_miss_date="2026-07-02",
    )
    decision = apply_lifecycle_observation(job, missing_observation(NOW))
    assert decision.changed is True
    assert job.status == "likely_closed"
    assert job.lifecycle_miss_count == 1
    assert job.lifecycle_last_authoritative_miss_date == "2026-07-02"
    assert job.closed_date == ""


def test_stale_observation_cannot_reverse_newer_state():
    job = make_job(
        status="reopened",
        enrichment_status="pending",
        lifecycle_last_checked_at="2026-07-02T18:00:00Z",
        lifecycle_next_check_at="2026-07-09T18:00:00Z",
        lifecycle_last_evidence_key="newer-evidence",
    )
    decision = apply_lifecycle_observation(
        job,
        LifecycleObservation(
            checked_at=NOW,
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


def test_reopened_queue_row_gets_fresh_retry_budget_and_clean_state():
    job = make_job(
        status="confirmed_closed",
        closed_date="2026-06-20",
        enrichment_status="closed",
        enrichment_source_url="https://careers.topgolf.com/jobs/123",
        enrichment_match_confidence=95,
    )
    old_row = EnrichmentQueueItem(
        enrichment_id="enr-1",
        job_key=job.job_key,
        status="closed",
        current_stage="external_search",
        attempt_count=8,
        next_attempt_at="2026-07-01T18:00:00Z",
        last_attempted_at="2026-06-20T18:00:00Z",
        matched_url="https://example.com/old-result",
        match_confidence=22,
        fields_recovered="title, location",
        error_type="posting_closed",
        error_message="old state",
        created_at="2026-06-01T18:00:00Z",
        updated_at="2026-06-20T18:00:00Z",
    )
    client = FakeSheetClient([job], [old_row])

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
    assert len(client.tables["Enrichment_Queue"]) == 2
    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    current_id = enrichment_id_for(job.job_key, job.canonical_url)
    assert rows["enr-1"]["status"] == "closed"
    row = rows[current_id]
    assert row["status"] == "pending"
    assert row["current_stage"] == "direct_url"
    assert row["attempt_count"] == 0
    assert row["next_attempt_at"] == ""
    assert row["last_attempted_at"] == ""
    assert row["matched_url"] == ""
    assert row["match_confidence"] is None
    assert row["fields_recovered"] == ""
    assert row["error_type"] == ""
    assert row["error_message"] == ""
    assert row["created_at"] == NOW
    assert row["updated_at"] == NOW


def test_stale_run_writes_audit_evidence_without_mutation():
    job = make_job(
        status="reopened",
        enrichment_status="pending",
        lifecycle_last_checked_at="2026-07-02T18:00:00Z",
        lifecycle_next_check_at="",
    )
    queue = [EnrichmentQueueItem(enrichment_id="enr-1", job_key=job.job_key, status="pending")]
    client = FakeSheetClient([job], queue)

    def checker(_job, *, checked_at):
        return missing_observation(checked_at)

    summary = run_lifecycle_checks(client, checker=checker, now=NOW, write_run_record=False)
    assert summary.jobs_updated == 0
    assert summary.jobs_unchanged == 1
    assert summary.evidence_written == 1
    assert client.tables["Jobs"][0]["status"] == "reopened"
    assert client.tables["Enrichment_Queue"][0]["status"] == "pending"
