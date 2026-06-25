from __future__ import annotations

import pytest

from src.enrichment.lifecycle import LifecycleObservation, run_lifecycle_checks
from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import enrichment_id_for
from src.models import JobPosting

NOW = "2026-06-25T18:00:00Z"
CURRENT_URL = "https://careers.topgolf.com/jobs/123"
OLD_URL = "https://www.linkedin.com/jobs/view/123"


def make_job(**overrides) -> JobPosting:
    values = {
        "job_key": "topgolf-123",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "canonical_url": CURRENT_URL,
        "source_job_id": "123",
        "source_primary": "gmail_alert",
        "first_seen_date": "2026-04-01",
        "last_seen_date": "2026-06-20",
        "status": "confirmed_closed",
        "closed_date": "2026-06-20",
        "potential_priority": "high",
        "potential_priority_score": 90,
        "score_status": "provisional",
        "enrichment_status": "closed",
        "enrichment_priority": "high",
        "enrichment_source_url": CURRENT_URL,
        "enrichment_match_confidence": 95,
    }
    values.update(overrides)
    return JobPosting(**values)


def authoritative_open(checked_at: str) -> LifecycleObservation:
    return LifecycleObservation(
        checked_at=checked_at,
        source_type="direct_url",
        source_url=CURRENT_URL,
        authoritative=True,
        http_status=200,
        listed=True,
    )


class PartialWriteSheetClient:
    def __init__(
        self,
        job: JobPosting,
        queue: list[EnrichmentQueueItem],
        *,
        fail_queue_append_once: bool = False,
        fail_evidence_append_once: bool = False,
    ) -> None:
        self.tables = {
            "Jobs": [job.to_dict()],
            "Enrichment_Queue": [item.to_dict() for item in queue],
            "Enrichment_Evidence": [],
            "Runs": [],
        }
        self.fail_queue_append_once = fail_queue_append_once
        self.fail_evidence_append_once = fail_evidence_append_once

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def read_jobs_with_row_numbers(self):
        return [(2, JobPosting.from_dict(self.tables["Jobs"][0]))]

    def append_record(self, worksheet_name, record):
        if worksheet_name == "Enrichment_Queue" and self.fail_queue_append_once:
            self.fail_queue_append_once = False
            raise RuntimeError("simulated queue append failure")
        if worksheet_name == "Enrichment_Evidence" and self.fail_evidence_append_once:
            self.fail_evidence_append_once = False
            raise RuntimeError("simulated evidence append failure")
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()


def unexpected_checker(_job, *, checked_at):
    raise AssertionError(f"recovery should not make a new lifecycle request at {checked_at}")


def test_queue_append_failure_is_repaired_before_missing_evidence_is_committed():
    job = make_job()
    old_row = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, OLD_URL),
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        lead_url=OLD_URL,
        priority="high",
        status="closed",
        current_stage="external_search",
        attempt_count=8,
        error_type="posting_closed",
    )
    client = PartialWriteSheetClient(job, [old_row], fail_queue_append_once=True)

    with pytest.raises(RuntimeError, match="queue append failure"):
        run_lifecycle_checks(
            client,
            checker=lambda _job, checked_at: authoritative_open(checked_at),
            now=NOW,
            write_run_record=False,
        )

    assert client.tables["Jobs"][0]["status"] == "reopened"
    assert client.tables["Jobs"][0]["lifecycle_next_check_at"] == "2026-07-02T18:00:00Z"
    assert client.tables["Enrichment_Evidence"] == []
    assert len(client.tables["Enrichment_Queue"]) == 1

    summary = run_lifecycle_checks(
        client,
        checker=unexpected_checker,
        now=NOW,
        write_run_record=False,
    )

    assert summary.jobs_checked == 1
    assert summary.jobs_updated == 0
    assert summary.jobs_unchanged == 1
    assert summary.evidence_written == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1
    assert client.tables["Enrichment_Evidence"][0]["source_type"] == "lifecycle_recovery"

    rows = {row["enrichment_id"]: row for row in client.tables["Enrichment_Queue"]}
    current_id = enrichment_id_for(job.job_key, CURRENT_URL)
    old_id = enrichment_id_for(job.job_key, OLD_URL)
    assert rows[current_id]["status"] == "pending"
    assert rows[current_id]["attempt_count"] == 0
    assert rows[old_id]["status"] == "closed"


def test_evidence_append_failure_recovery_preserves_an_active_retry_cycle():
    job = make_job()
    current_row = EnrichmentQueueItem(
        enrichment_id=enrichment_id_for(job.job_key, CURRENT_URL),
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        lead_url=CURRENT_URL,
        priority="high",
        status="closed",
        current_stage="external_search",
        attempt_count=6,
        error_type="posting_closed",
    )
    client = PartialWriteSheetClient(job, [current_row], fail_evidence_append_once=True)

    with pytest.raises(RuntimeError, match="evidence append failure"):
        run_lifecycle_checks(
            client,
            checker=lambda _job, checked_at: authoritative_open(checked_at),
            now=NOW,
            write_run_record=False,
        )

    assert client.tables["Jobs"][0]["status"] == "reopened"
    assert client.tables["Enrichment_Evidence"] == []
    row = client.tables["Enrichment_Queue"][0]
    assert row["status"] == "pending"
    assert row["attempt_count"] == 0

    row["status"] = "retryable_failure"
    row["attempt_count"] = 1
    row["last_attempted_at"] = NOW
    row["next_attempt_at"] = "2026-06-26T18:00:00Z"
    row["error_type"] = "network_retryable"
    row["error_message"] = "temporary timeout"

    summary = run_lifecycle_checks(
        client,
        checker=unexpected_checker,
        now=NOW,
        write_run_record=False,
    )

    assert summary.jobs_checked == 1
    assert summary.jobs_updated == 0
    assert summary.jobs_unchanged == 1
    assert summary.evidence_written == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1

    recovered_row = client.tables["Enrichment_Queue"][0]
    assert recovered_row["status"] == "retryable_failure"
    assert recovered_row["attempt_count"] == 1
    assert recovered_row["last_attempted_at"] == NOW
    assert recovered_row["next_attempt_at"] == "2026-06-26T18:00:00Z"
    assert recovered_row["error_type"] == "network_retryable"
