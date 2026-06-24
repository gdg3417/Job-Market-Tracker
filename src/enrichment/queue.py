from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from src.enrichment.models import EnrichmentQueueItem, utc_now_iso
from src.models import JobPosting

OPEN_STATUSES = {"open", "reopened"}
PROCESSABLE_QUEUE_STATUSES = {"pending", "retryable_failure"}
STALE_IN_PROGRESS_AFTER = timedelta(minutes=30)


@dataclass(slots=True)
class QueueEnqueueSummary:
    jobs_evaluated: int = 0
    created: int = 0
    existing: int = 0
    refreshed: int = 0


def normalize_lead_url(value: str) -> str:
    candidate = str(value or "").strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def enrichment_id_for(job_key: str, lead_url: str, stage: str = "direct_url") -> str:
    material = "|".join([str(job_key or "").strip(), normalize_lead_url(lead_url), stage])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"enr_{digest}"


def evidence_id_for(enrichment_id: str, source_url: str, raw_content_hash: str) -> str:
    material = "|".join([str(enrichment_id or "").strip(), normalize_lead_url(source_url), str(raw_content_hash or "").strip()])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"ev_{digest}"


def job_is_direct_link_eligible(job: JobPosting) -> bool:
    return (
        job.status in OPEN_STATUSES
        and job.score_status in {"provisional", "partially_verified"}
        and job.potential_priority in {"high", "medium"}
        and job.enrichment_status in {"pending", "retryable_failure"}
        and bool(str(job.job_key or "").strip())
        and bool(str(job.title or "").strip())
        and bool(str(job.company or "").strip())
        and bool(normalize_lead_url(job.canonical_url))
    )


def _records_with_rows(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    records = list(sheet_client.read_records(worksheet_name))
    return [(index + 2, record) for index, record in enumerate(records)]


def _jobs_with_rows(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [
        (row_number, JobPosting.from_dict(record))
        for row_number, record in _records_with_rows(sheet_client, "Jobs")
        if any(str(record.get(key, "")).strip() for key in ("job_key", "company", "title", "canonical_url"))
    ]


def _queue_changed(existing: EnrichmentQueueItem, desired: EnrichmentQueueItem) -> bool:
    fields = ("company", "title", "location", "source_job_id", "lead_url", "priority")
    return any(getattr(existing, field_name) != getattr(desired, field_name) for field_name in fields)


def enqueue_eligible_jobs(
    sheet_client: Any,
    *,
    jobs: Iterable[tuple[int, JobPosting]] | None = None,
    now: str | None = None,
) -> tuple[QueueEnqueueSummary, list[tuple[int, EnrichmentQueueItem]]]:
    timestamp = now or utc_now_iso()
    job_rows = list(jobs) if jobs is not None else _jobs_with_rows(sheet_client)
    queue_rows = _records_with_rows(sheet_client, "Enrichment_Queue")
    existing_by_id = {
        item.enrichment_id: (row_number, item)
        for row_number, record in queue_rows
        if (item := EnrichmentQueueItem.from_dict(record)).enrichment_id
    }
    summary = QueueEnqueueSummary()

    for _, job in job_rows:
        summary.jobs_evaluated += 1
        if not job_is_direct_link_eligible(job):
            continue
        queue_id = enrichment_id_for(job.job_key, job.canonical_url)
        desired = EnrichmentQueueItem(
            enrichment_id=queue_id,
            job_key=job.job_key,
            company=job.company,
            title=job.title,
            location=job.location,
            source_job_id=job.source_job_id,
            lead_url=normalize_lead_url(job.canonical_url),
            priority=job.enrichment_priority or job.potential_priority,
            status="pending",
            current_stage="direct_url",
            created_at=timestamp,
            updated_at=timestamp,
        )
        existing_match = existing_by_id.get(queue_id)
        if existing_match is None:
            sheet_client.append_record("Enrichment_Queue", desired.to_dict())
            next_row = max([row_number for row_number, _ in queue_rows], default=1) + 1
            queue_rows.append((next_row, desired.to_dict()))
            existing_by_id[queue_id] = (next_row, desired)
            summary.created += 1
            continue

        row_number, existing = existing_match
        summary.existing += 1
        if _queue_changed(existing, desired):
            existing.company = desired.company
            existing.title = desired.title
            existing.location = desired.location
            existing.source_job_id = desired.source_job_id
            existing.lead_url = desired.lead_url
            existing.priority = desired.priority
            existing.updated_at = timestamp
            sheet_client.update_record("Enrichment_Queue", row_number, existing.to_dict())
            existing_by_id[queue_id] = (row_number, existing)
            summary.refreshed += 1

    return summary, sorted(existing_by_id.values(), key=lambda pair: pair[0])


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def due_for_processing(item: EnrichmentQueueItem, *, now: str) -> bool:
    current_time = _parse_timestamp(now)
    if current_time is None:
        return False

    if item.status == "in_progress":
        last_attempt = _parse_timestamp(item.last_attempted_at or item.updated_at)
        return last_attempt is None or current_time - last_attempt >= STALE_IN_PROGRESS_AFTER

    if item.status not in PROCESSABLE_QUEUE_STATUSES:
        return False
    if not item.next_attempt_at:
        return True
    next_attempt = _parse_timestamp(item.next_attempt_at)
    return next_attempt is not None and next_attempt <= current_time


def priority_sort_key(item: EnrichmentQueueItem) -> tuple[int, str, str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(item.priority, 3)
    return priority_rank, item.created_at, item.enrichment_id
