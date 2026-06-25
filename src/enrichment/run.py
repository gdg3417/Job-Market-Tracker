from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.enrichment.extractors import extract_job_evidence
from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError
from src.enrichment.lifecycle import max_attempts_for_priority, next_retry_at
from src.enrichment.matcher import assess_match
from src.enrichment.merge import merge_verified_evidence
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem, EnrichmentRunSummary, utc_now_iso
from src.enrichment.queue import (
    due_for_processing,
    enqueue_eligible_jobs,
    enrichment_id_for,
    evidence_id_for,
    job_is_direct_link_eligible,
    normalize_lead_url,
    priority_sort_key,
)
from src.models import JobPosting

DEFAULT_PRIORITY_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "potential_priority_rules.yml"
DIRECT_FALLBACK_ERROR_TYPES = {"not_found", "access_blocked"}
REPLAYABLE_QUEUE_STATUSES = {"not_found", "ambiguous", "permanent_failure"}
ACTIVE_DIRECT_QUEUE_STATUSES = {"pending", "retryable_failure", "in_progress"}


def _records_with_rows(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    return [(index + 2, record) for index, record in enumerate(sheet_client.read_records(worksheet_name))]


def _jobs_with_rows(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [
        (row_number, JobPosting.from_dict(record))
        for row_number, record in _records_with_rows(sheet_client, "Jobs")
        if any(str(record.get(key, "")).strip() for key in ("job_key", "company", "title", "canonical_url"))
    ]


def _update_job(sheet_client: Any, row_number: int, job: JobPosting) -> None:
    if hasattr(sheet_client, "update_job"):
        sheet_client.update_job(row_number, job)
    else:
        sheet_client.update_record("Jobs", row_number, job.to_dict())


def _load_priority_rules(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        path = DEFAULT_PRIORITY_RULES_PATH
    from src.potential_priority import load_potential_priority_rules

    return load_potential_priority_rules(path)


def _existing_evidence(sheet_client: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    return {
        str(record.get("evidence_id") or "").strip(): (row_number, record)
        for row_number, record in _records_with_rows(sheet_client, "Enrichment_Evidence")
        if str(record.get("evidence_id") or "").strip()
    }


def _write_evidence(
    sheet_client: Any,
    evidence: EnrichmentEvidence,
    existing: dict[str, tuple[int, dict[str, Any]]],
) -> bool:
    record = evidence.to_dict()
    current = existing.get(evidence.evidence_id)
    if current is None:
        sheet_client.append_record("Enrichment_Evidence", record)
        next_row = max((row_number for row_number, _ in existing.values()), default=1) + 1
        existing[evidence.evidence_id] = (next_row, record)
        return True
    row_number, prior = current
    if prior != record:
        sheet_client.update_record("Enrichment_Evidence", row_number, record)
        existing[evidence.evidence_id] = (row_number, record)
        return True
    return False


def _job_error_status(queue_status: str) -> str:
    return {
        "retryable_failure": "retryable_failure",
        "permanent_failure": "permanent_failure",
        "not_found": "not_found",
        "ambiguous": "ambiguous",
    }.get(queue_status, "retryable_failure")


def _record_failure_evidence(
    *,
    item: EnrichmentQueueItem,
    now: str,
    status_code: int | None,
    source_url: str,
    error_type: str,
    message: str,
) -> EnrichmentEvidence:
    content_key = f"failure:{item.attempt_count}:{error_type}:{status_code or ''}"
    content_hash = hashlib.sha256(content_key.encode("utf-8")).hexdigest()
    evidence = EnrichmentEvidence(
        job_key=item.job_key,
        enrichment_id=item.enrichment_id,
        source_type="direct_url_failure",
        source_url=source_url or item.lead_url,
        retrieved_at=now,
        http_status=status_code,
        accepted=False,
        rejection_reason=f"{error_type}: {message}"[:1000],
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, content_hash)
    return evidence


def _prepare_replay(
    sheet_client: Any,
    *,
    queue_rows: list[tuple[int, EnrichmentQueueItem]],
    job_by_key: dict[str, tuple[int, JobPosting]],
    job_key: str,
    timestamp: str,
) -> tuple[int, EnrichmentQueueItem]:
    if not job_key:
        raise ValueError("Replay requires an exact job_key")
    target = job_by_key.get(job_key)
    if target is None:
        raise ValueError(f"Replay job not found: {job_key}")
    job_row_number, job = target
    expected_id = enrichment_id_for(job.job_key, job.canonical_url)
    queue_match = next((pair for pair in queue_rows if pair[1].enrichment_id == expected_id), None)
    if queue_match is None:
        raise ValueError(f"Replay requires an existing queue row for job_key={job_key}")
    queue_row_number, item = queue_match
    if item.status not in REPLAYABLE_QUEUE_STATUSES:
        raise ValueError(f"Queue status {item.status!r} is not replayable for job_key={job_key}")

    item.status = "pending"
    item.next_attempt_at = ""
    item.updated_at = timestamp
    sheet_client.update_record("Enrichment_Queue", queue_row_number, item.to_dict())
    job.enrichment_status = "pending"
    _update_job(sheet_client, job_row_number, job)
    return queue_row_number, item


def _set_retry_state(item: EnrichmentQueueItem, job: JobPosting, *, timestamp: str, retryable: bool) -> None:
    item.status = "retryable_failure" if retryable else "permanent_failure"
    item.next_attempt_at = next_retry_at(item.attempt_count, timestamp) if retryable else ""
    job.enrichment_status = _job_error_status(item.status)


def _preferred_queue_rows(
    queue_rows: list[tuple[int, EnrichmentQueueItem]],
    job_by_key: dict[str, tuple[int, JobPosting]],
) -> list[tuple[int, EnrichmentQueueItem]]:
    """Return at most one queue row per job, preferring the current canonical URL even when it is not due."""
    grouped: dict[str, list[tuple[int, EnrichmentQueueItem]]] = {}
    for pair in queue_rows:
        item = pair[1]
        if item.job_key in job_by_key:
            grouped.setdefault(item.job_key, []).append(pair)

    selected: list[tuple[int, EnrichmentQueueItem]] = []
    for current_job_key, rows in grouped.items():
        job = job_by_key[current_job_key][1]
        expected_id = enrichment_id_for(job.job_key, job.canonical_url)
        exact = [pair for pair in rows if pair[1].enrichment_id == expected_id]
        if exact:
            selected.append(min(exact, key=lambda pair: pair[0]))
            continue

        canonical_url = normalize_lead_url(job.canonical_url)
        matching_url = [pair for pair in rows if normalize_lead_url(pair[1].lead_url) == canonical_url]
        if matching_url:
            selected.append(min(matching_url, key=lambda pair: pair[0]))
            continue

        active = [pair for pair in rows if pair[1].status in ACTIVE_DIRECT_QUEUE_STATUSES]
        if active:
            selected.append(min(active, key=lambda pair: pair[0]))

    return selected


def run_direct_link_enrichment(
    sheet_client: Any,
    *,
    limit: int = 10,
    fetcher: DirectLinkFetcher | Any | None = None,
    now: str | None = None,
    job_key: str = "",
    priority_rules: dict[str, Any] | None = None,
    replay: bool = False,
) -> EnrichmentRunSummary:
    timestamp = now or utc_now_iso()
    fetcher = fetcher or DirectLinkFetcher()
    rules = priority_rules if priority_rules is not None else _load_priority_rules(None)
    summary = EnrichmentRunSummary()

    job_rows = _jobs_with_rows(sheet_client)
    if job_key:
        job_rows = [(row_number, job) for row_number, job in job_rows if job.job_key == job_key]
    job_by_key = {job.job_key: (row_number, job) for row_number, job in job_rows if job.job_key}

    enqueue_summary, queue_rows = enqueue_eligible_jobs(sheet_client, jobs=job_rows, now=timestamp)
    summary.jobs_evaluated = enqueue_summary.jobs_evaluated
    summary.jobs_enqueued = enqueue_summary.created
    summary.queue_existing = enqueue_summary.existing

    if replay:
        _prepare_replay(
            sheet_client,
            queue_rows=queue_rows,
            job_by_key=job_by_key,
            job_key=job_key,
            timestamp=timestamp,
        )
        summary.queue_existing = max(1, summary.queue_existing)

    evidence_by_id = _existing_evidence(sheet_client)
    preferred_rows = _preferred_queue_rows(queue_rows, job_by_key)
    due = [pair for pair in preferred_rows if due_for_processing(pair[1], now=timestamp)]
    due.sort(key=lambda pair: priority_sort_key(pair[1]))

    for queue_row_number, item in due[: max(0, limit)]:
        job_row_number, job = job_by_key[item.job_key]
        summary.direct_attempts += 1
        item.status = "in_progress"
        item.current_stage = "direct_url"
        item.attempt_count += 1
        item.last_attempted_at = timestamp
        item.next_attempt_at = ""
        item.matched_url = ""
        item.match_confidence = None
        item.fields_recovered = ""
        item.error_type = ""
        item.error_message = ""
        item.updated_at = timestamp
        sheet_client.update_record("Enrichment_Queue", queue_row_number, item.to_dict())

        job.enrichment_status = "in_progress"
        job.enrichment_last_attempted_at = timestamp
        _update_job(sheet_client, job_row_number, job)

        try:
            fetched = fetcher.fetch(item.lead_url)
            evidence = extract_job_evidence(
                fetched,
                job_key=item.job_key,
                enrichment_id=item.enrichment_id,
                retrieved_at=timestamp,
            )
            if evidence is None:
                item.status = "not_found"
                item.matched_url = fetched.final_url
                item.match_confidence = 0
                item.error_type = "non_job_page"
                item.error_message = "Direct URL did not contain a specific job posting"
                failure_evidence = _record_failure_evidence(
                    item=item,
                    now=timestamp,
                    status_code=fetched.status_code,
                    source_url=fetched.final_url,
                    error_type=item.error_type,
                    message=item.error_message,
                )
                if _write_evidence(sheet_client, failure_evidence, evidence_by_id):
                    summary.evidence_written += 1
                job.enrichment_status = "not_found"
                job.enrichment_match_confidence = 0
                job.enrichment_source_url = fetched.final_url
                summary.not_found += 1
            else:
                match = assess_match(job, evidence)
                evidence.match_confidence = match.confidence
                evidence.accepted = match.accepted
                evidence.rejection_reason = "" if match.accepted else "; ".join(match.reasons)
                evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, evidence.raw_content_hash)
                if _write_evidence(sheet_client, evidence, evidence_by_id):
                    summary.evidence_written += 1

                item.matched_url = evidence.canonical_url or evidence.source_url
                item.match_confidence = match.confidence
                item.fields_recovered = ", ".join(evidence.recovered_fields())
                if match.accepted:
                    job, changed_fields = merge_verified_evidence(
                        job,
                        evidence,
                        match_confidence=match.confidence,
                        evidence_rules=rules,
                        completed_at=timestamp,
                    )
                    item.status = job.enrichment_status
                    item.error_type = ""
                    item.error_message = ""
                    summary.enriched += int(item.status == "enriched")
                    summary.partial += int(item.status == "partial")
                    summary.jobs_updated += int(bool(changed_fields) or item.status in {"enriched", "partial"})
                elif match.outcome == "ambiguous":
                    item.status = "ambiguous"
                    item.error_type = "ambiguous_match"
                    item.error_message = "; ".join(match.reasons)[:1000]
                    job.enrichment_status = "ambiguous"
                    job.enrichment_match_confidence = match.confidence
                    job.enrichment_source_url = evidence.source_url
                    summary.ambiguous += 1
                else:
                    item.status = "not_found"
                    item.error_type = "mismatched_posting"
                    item.error_message = "; ".join(match.reasons)[:1000]
                    job.enrichment_status = "not_found"
                    job.enrichment_match_confidence = match.confidence
                    job.enrichment_source_url = evidence.source_url
                    summary.not_found += 1
        except EnrichmentFetchError as exc:
            direct_fallback = exc.error_type in DIRECT_FALLBACK_ERROR_TYPES
            maximum = max_attempts_for_priority(item.priority)
            retryable = exc.retryable and not direct_fallback and item.attempt_count < maximum
            if direct_fallback:
                item.status = "not_found"
                item.next_attempt_at = ""
                job.enrichment_status = "not_found"
            else:
                _set_retry_state(item, job, timestamp=timestamp, retryable=retryable)
            item.error_type = exc.error_type
            item.error_message = str(exc)[:1000]
            item.matched_url = exc.final_url
            job.enrichment_source_url = exc.final_url or item.lead_url
            failure_evidence = _record_failure_evidence(
                item=item,
                now=timestamp,
                status_code=exc.status_code,
                source_url=exc.final_url or item.lead_url,
                error_type=exc.error_type,
                message=str(exc),
            )
            if _write_evidence(sheet_client, failure_evidence, evidence_by_id):
                summary.evidence_written += 1
            if direct_fallback:
                summary.not_found += 1
            elif retryable:
                summary.retryable_failures += 1
            else:
                summary.permanent_failures += 1
        except Exception as exc:
            maximum = max_attempts_for_priority(item.priority)
            retryable = item.attempt_count < maximum
            _set_retry_state(item, job, timestamp=timestamp, retryable=retryable)
            item.error_type = "unexpected_error"
            item.error_message = str(exc)[:1000]
            job.enrichment_source_url = item.lead_url
            failure_evidence = _record_failure_evidence(
                item=item,
                now=timestamp,
                status_code=None,
                source_url=item.lead_url,
                error_type=item.error_type,
                message=str(exc),
            )
            if _write_evidence(sheet_client, failure_evidence, evidence_by_id):
                summary.evidence_written += 1
            if retryable:
                summary.retryable_failures += 1
            else:
                summary.permanent_failures += 1

        item.updated_at = timestamp
        job.enrichment_last_attempted_at = timestamp
        if hasattr(job, "refresh_updated_at"):
            job.refresh_updated_at()
        sheet_client.update_record("Enrichment_Queue", queue_row_number, item.to_dict())
        _update_job(sheet_client, job_row_number, job)

    return summary


def preview_direct_link_queue(sheet_client: Any, *, job_key: str = "") -> dict[str, Any]:
    jobs = _jobs_with_rows(sheet_client)
    eligible = [job for _, job in jobs if job_is_direct_link_eligible(job) and (not job_key or job.job_key == job_key)]
    return {
        "eligible_jobs": len(eligible),
        "jobs": [
            {
                "job_key": job.job_key,
                "company": job.company,
                "title": job.title,
                "lead_url": job.canonical_url,
                "priority": job.enrichment_priority or job.potential_priority,
            }
            for job in eligible
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue and process direct-link job enrichment")
    parser.add_argument("--run", action="store_true", help="Migrate worksheets, enqueue eligible jobs, and process direct URLs")
    parser.add_argument("--dry-run", action="store_true", help="Show eligible jobs without changing the workbook")
    parser.add_argument("--limit", type=int, default=10, help="Maximum direct URLs to process")
    parser.add_argument("--job-key", default="", help="Restrict the run to one existing Jobs job_key")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay one existing terminal queue item after a parser or matcher fix; requires --run and --job-key",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.run and not args.dry_run:
        raise SystemExit("Choose --run or --dry-run")
    if args.replay and (not args.run or not args.job_key):
        raise SystemExit("--replay requires --run and an exact --job-key")

    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        print(json.dumps(preview_direct_link_queue(sheet_client, job_key=args.job_key), indent=2))
        return

    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    summary = run_direct_link_enrichment(
        sheet_client,
        limit=args.limit,
        job_key=args.job_key,
        replay=args.replay,
    )
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
