from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from src.enrichment.extractors import extract_job_evidence
from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem, utc_now_iso
from src.enrichment.queue import evidence_id_for
from src.models import JobPosting, days_between, parse_iso_date, today_iso

OPEN_JOB_STATUSES = {"open", "reopened", "not_seen_once", "likely_closed"}
TERMINAL_JOB_STATUSES = {"confirmed_closed", "closed", "expired"}
QUEUE_RETRY_STATUSES = {"retryable_failure", "not_found", "permanent_failure"}
KNOWN_AGGREGATOR_HOSTS = {
    "indeed.com",
    "www.indeed.com",
    "linkedin.com",
    "www.linkedin.com",
    "glassdoor.com",
    "www.glassdoor.com",
    "ziprecruiter.com",
    "www.ziprecruiter.com",
}
KNOWN_ATS_HOST_TERMS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "workdayjobs.com",
    "icims.com",
    "phenompeople.com",
    "oraclecloud.com",
    "successfactors.com",
)
CLOSED_TEXT_PATTERNS = (
    r"job (?:is )?no longer available",
    r"position (?:has been|is) filled",
    r"posting (?:has )?expired",
    r"job (?:has )?expired",
    r"no longer accepting applications",
    r"this vacancy (?:has )?closed",
)
GENERIC_PATHS = {"", "/", "/jobs", "/jobs/", "/careers", "/careers/", "/search", "/search/"}
TRANSIENT_ERROR_TYPES = {"http_retryable", "timeout", "connection_error", "unexpected_error"}


@dataclass(frozen=True, slots=True)
class LifecyclePolicy:
    authoritative_misses_to_close: int = 2
    gmail_likely_closed_after_days: int = 45
    gmail_supporting_misses_required: int = 3
    open_check_interval_days: int = 7
    terminal_check_interval_days: int = 14
    high_priority_max_attempts: int = 8
    medium_priority_max_attempts: int = 6
    low_priority_max_attempts: int = 4


@dataclass(frozen=True, slots=True)
class LifecycleObservation:
    checked_at: str
    source_type: str
    source_url: str = ""
    authoritative: bool = False
    http_status: int | None = None
    listed: bool | None = None
    explicitly_closed: bool = False
    redirected_to_generic: bool = False
    valid_through: str = ""
    error_type: str = ""
    message: str = ""
    supporting_absence: bool = False

    @property
    def evidence_key(self) -> str:
        payload = {
            "checked_at": self.checked_at,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "authoritative": self.authoritative,
            "http_status": self.http_status,
            "listed": self.listed,
            "explicitly_closed": self.explicitly_closed,
            "redirected_to_generic": self.redirected_to_generic,
            "valid_through": self.valid_through,
            "error_type": self.error_type,
            "message": self.message,
            "supporting_absence": self.supporting_absence,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    previous_status: str
    status: str
    changed: bool
    reason: str
    evidence_type: str
    evidence_key: str
    lifecycle_miss_count: int


@dataclass(slots=True)
class LifecycleRunSummary:
    jobs_evaluated: int = 0
    jobs_checked: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    temporary_failures: int = 0
    likely_closed: int = 0
    confirmed_closed: int = 0
    expired: int = 0
    reopened: int = 0
    open_confirmed: int = 0
    evidence_written: int = 0
    queue_retries_scheduled: int = 0
    queue_permanent_failures: int = 0
    duplicate_observations: int = 0
    health_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def _iso_after(value: str, *, days: int) -> str:
    parsed = _parse_timestamp(value) or datetime.now(UTC)
    return (parsed + timedelta(days=max(0, days))).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def retry_delay_days(attempt_count: int) -> int:
    """Return the Sprint 31 retry delay after a completed attempt."""
    if attempt_count <= 0:
        return 0
    if attempt_count == 1:
        return 1
    if attempt_count == 2:
        return 3
    return 7


def next_retry_at(attempt_count: int, now: str) -> str:
    return _iso_after(now, days=retry_delay_days(attempt_count))


def max_attempts_for_priority(priority: str, policy: LifecyclePolicy | None = None) -> int:
    rules = policy or LifecyclePolicy()
    return {
        "high": rules.high_priority_max_attempts,
        "medium": rules.medium_priority_max_attempts,
        "low": rules.low_priority_max_attempts,
    }.get(str(priority or "").strip().lower(), rules.low_priority_max_attempts)


def schedule_enrichment_retry(
    item: EnrichmentQueueItem,
    *,
    now: str,
    policy: LifecyclePolicy | None = None,
) -> bool:
    """Schedule a declining retry cadence without reopening manual-review matches."""
    if item.status not in QUEUE_RETRY_STATUSES:
        return False
    maximum = max_attempts_for_priority(item.priority, policy)
    if item.attempt_count >= maximum:
        changed = item.status != "permanent_failure" or bool(item.next_attempt_at)
        item.status = "permanent_failure"
        item.next_attempt_at = ""
        item.updated_at = now
        return changed
    desired = next_retry_at(item.attempt_count, now)
    changed = item.status != "retryable_failure" or item.next_attempt_at != desired
    item.status = "retryable_failure"
    item.next_attempt_at = desired
    item.updated_at = now
    return changed


def _is_expired(valid_through: str, checked_at: str) -> bool:
    expiry = parse_iso_date(valid_through)
    checked = parse_iso_date(checked_at)
    return bool(expiry and checked and expiry < checked)


def _is_temporary_failure(observation: LifecycleObservation) -> bool:
    return (
        observation.error_type in TRANSIENT_ERROR_TYPES
        or observation.http_status == 429
        or (observation.http_status is not None and observation.http_status >= 500)
    )


def _is_authoritative_absence(observation: LifecycleObservation) -> bool:
    return observation.authoritative and (
        observation.http_status in {404, 410}
        or observation.listed is False
        or observation.redirected_to_generic
    )


def _is_open_signal(observation: LifecycleObservation) -> bool:
    return observation.listed is True and not observation.explicitly_closed and not _is_expired(
        observation.valid_through, observation.checked_at
    )


def _evidence_type(observation: LifecycleObservation) -> str:
    if observation.explicitly_closed:
        return "explicitly_closed"
    if _is_expired(observation.valid_through, observation.checked_at):
        return "valid_through_expired"
    if observation.http_status in {404, 410} and observation.authoritative:
        return "authoritative_http_missing"
    if observation.listed is False and observation.authoritative:
        return "authoritative_listing_missing"
    if observation.redirected_to_generic and observation.authoritative:
        return "authoritative_generic_redirect"
    if _is_open_signal(observation):
        return "authoritative_open"
    if _is_temporary_failure(observation):
        return "temporary_failure"
    if observation.supporting_absence:
        return "supporting_absence"
    return "unresolved"


def apply_lifecycle_observation(
    job: JobPosting,
    observation: LifecycleObservation,
    *,
    policy: LifecyclePolicy | None = None,
) -> LifecycleDecision:
    rules = policy or LifecyclePolicy()
    previous_status = job.status
    evidence_key = observation.evidence_key
    evidence_type = _evidence_type(observation)

    if evidence_key == job.lifecycle_last_evidence_key:
        return LifecycleDecision(
            previous_status=previous_status,
            status=job.status,
            changed=False,
            reason=job.lifecycle_reason or "duplicate lifecycle observation",
            evidence_type=evidence_type,
            evidence_key=evidence_key,
            lifecycle_miss_count=job.lifecycle_miss_count,
        )

    job.lifecycle_check_count += 1
    job.lifecycle_last_checked_at = observation.checked_at
    job.lifecycle_last_evidence_key = evidence_key
    job.lifecycle_evidence_type = evidence_type
    job.lifecycle_evidence_url = observation.source_url
    job.lifecycle_evidence_at = observation.checked_at
    check_interval = rules.terminal_check_interval_days if job.status in TERMINAL_JOB_STATUSES else rules.open_check_interval_days
    job.lifecycle_next_check_at = _iso_after(observation.checked_at, days=check_interval)

    reason = "No authoritative lifecycle conclusion"
    if observation.explicitly_closed:
        job.status = "confirmed_closed"
        job.closed_date = str(parse_iso_date(observation.checked_at) or today_iso())
        job.lifecycle_miss_count = max(job.lifecycle_miss_count, rules.authoritative_misses_to_close)
        reason = "Authoritative posting explicitly reports the role is closed"
    elif _is_expired(observation.valid_through, observation.checked_at):
        job.mark_expired(str(parse_iso_date(observation.valid_through) or parse_iso_date(observation.checked_at) or today_iso()))
        job.lifecycle_miss_count = max(job.lifecycle_miss_count, rules.authoritative_misses_to_close)
        reason = f"Structured validThrough expired on {str(parse_iso_date(observation.valid_through))}"
    elif _is_open_signal(observation):
        job.lifecycle_miss_count = 0
        job.closed_date = ""
        if previous_status in TERMINAL_JOB_STATUSES or previous_status in {"likely_closed", "not_seen_once"}:
            job.status = "reopened"
            reason = "Authoritative posting was rediscovered after a closure signal"
        else:
            job.status = "open"
            reason = "Authoritative posting remains available"
        job.last_seen_date = str(parse_iso_date(observation.checked_at) or today_iso())
    elif _is_authoritative_absence(observation):
        job.lifecycle_miss_count += 1
        if job.lifecycle_miss_count >= rules.authoritative_misses_to_close:
            job.status = "confirmed_closed"
            job.closed_date = str(parse_iso_date(observation.checked_at) or today_iso())
            reason = "Repeated authoritative absence confirms the posting is closed"
        else:
            job.status = "likely_closed"
            reason = "One authoritative absence requires confirmation before closure"
    elif _is_temporary_failure(observation):
        reason = "Temporary retrieval failure does not change posting status"
    elif (
        "gmail" in str(job.source_primary or "").lower()
        and observation.supporting_absence
        and days_between(job.first_seen_date, observation.checked_at) >= rules.gmail_likely_closed_after_days
    ):
        job.lifecycle_miss_count += 1
        if job.lifecycle_miss_count >= rules.gmail_supporting_misses_required:
            job.status = "likely_closed"
            reason = "Aged Gmail-only role has repeated supporting absence but no authoritative closure"
        else:
            reason = "Aged Gmail-only role remains reviewable pending additional supporting absence"
    else:
        reason = "Unresolved or non-authoritative evidence does not close the job"

    job.lifecycle_reason = reason
    job.days_open = days_between(job.first_seen_date, job.closed_date or observation.checked_at)
    job.refresh_updated_at()
    changed = any(
        [
            previous_status != job.status,
            job.lifecycle_last_checked_at == observation.checked_at,
            job.lifecycle_last_evidence_key == evidence_key,
        ]
    )
    return LifecycleDecision(
        previous_status=previous_status,
        status=job.status,
        changed=changed,
        reason=reason,
        evidence_type=evidence_type,
        evidence_key=evidence_key,
        lifecycle_miss_count=job.lifecycle_miss_count,
    )


def _host(url: str) -> str:
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def is_authoritative_lifecycle_url(url: str) -> bool:
    host = _host(url)
    if not host or host in KNOWN_AGGREGATOR_HOSTS:
        return False
    if any(term in host for term in KNOWN_ATS_HOST_TERMS):
        return True
    return True


def _looks_generic_redirect(requested_url: str, final_url: str) -> bool:
    try:
        requested = urlsplit(requested_url)
        final = urlsplit(final_url)
    except ValueError:
        return False
    if not final.netloc or requested.netloc.lower() != final.netloc.lower():
        return False
    requested_path = requested.path.rstrip("/") or "/"
    final_path = final.path.rstrip("/") or "/"
    return requested_path != final_path and final_path in GENERIC_PATHS


def _explicit_closed_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    return any(re.search(pattern, normalized) for pattern in CLOSED_TEXT_PATTERNS)


class DirectUrlLifecycleChecker:
    def __init__(self, fetcher: DirectLinkFetcher | Any | None = None) -> None:
        self.fetcher = fetcher or DirectLinkFetcher()

    def __call__(self, job: JobPosting, *, checked_at: str) -> LifecycleObservation:
        source_url = str(job.enrichment_source_url or job.canonical_url or "").strip()
        if not source_url:
            return LifecycleObservation(
                checked_at=checked_at,
                source_type="missing_url",
                source_url="",
                authoritative=False,
                error_type="missing_url",
                message="No lifecycle URL is available",
            )
        authoritative = is_authoritative_lifecycle_url(source_url)
        try:
            fetched = self.fetcher.fetch(source_url)
            explicit_closed = _explicit_closed_text(fetched.text)
            generic_redirect = _looks_generic_redirect(source_url, fetched.final_url)
            extracted = extract_job_evidence(
                fetched,
                job_key=job.job_key,
                enrichment_id=f"lifecycle_{job.job_key}",
                retrieved_at=checked_at,
            )
            listed = bool(extracted and extracted.source_title and extracted.description_text)
            return LifecycleObservation(
                checked_at=checked_at,
                source_type="direct_url",
                source_url=fetched.final_url,
                authoritative=authoritative,
                http_status=fetched.status_code,
                listed=listed if listed else None,
                explicitly_closed=explicit_closed,
                redirected_to_generic=generic_redirect,
                valid_through=extracted.valid_through if extracted else "",
                supporting_absence=generic_redirect or explicit_closed,
            )
        except EnrichmentFetchError as exc:
            return LifecycleObservation(
                checked_at=checked_at,
                source_type="direct_url_failure",
                source_url=exc.final_url or source_url,
                authoritative=authoritative,
                http_status=exc.status_code,
                listed=False if exc.status_code in {404, 410} else None,
                error_type=exc.error_type,
                message=str(exc),
                supporting_absence=exc.status_code in {404, 410},
            )
        except Exception as exc:
            return LifecycleObservation(
                checked_at=checked_at,
                source_type="direct_url_failure",
                source_url=source_url,
                authoritative=authoritative,
                error_type="unexpected_error",
                message=str(exc),
            )


def _records(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    return [(index + 2, row) for index, row in enumerate(sheet_client.read_records(worksheet_name))]


def _jobs(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [(row_number, JobPosting.from_dict(row)) for row_number, row in _records(sheet_client, "Jobs")]


def _update_job(sheet_client: Any, row_number: int, job: JobPosting) -> None:
    if hasattr(sheet_client, "update_job"):
        sheet_client.update_job(row_number, job)
    else:
        sheet_client.update_record("Jobs", row_number, job.to_dict())


def _is_due(job: JobPosting, now: str) -> bool:
    if not job.lifecycle_next_check_at:
        return True
    due = _parse_timestamp(job.lifecycle_next_check_at)
    current = _parse_timestamp(now)
    return due is not None and current is not None and due <= current


def _lifecycle_evidence(job: JobPosting, observation: LifecycleObservation, decision: LifecycleDecision) -> EnrichmentEvidence:
    enrichment_id = f"lifecycle_{hashlib.sha256(job.job_key.encode('utf-8')).hexdigest()[:24]}"
    evidence = EnrichmentEvidence(
        job_key=job.job_key,
        enrichment_id=enrichment_id,
        source_type=f"lifecycle_{observation.source_type}",
        source_url=observation.source_url,
        retrieved_at=observation.checked_at,
        http_status=observation.http_status,
        canonical_url=observation.source_url,
        valid_through=observation.valid_through,
        raw_content_hash=decision.evidence_key,
        accepted=False,
        rejection_reason=json.dumps(
            {
                "status": decision.status,
                "reason": decision.reason,
                "evidence_type": decision.evidence_type,
                "authoritative": observation.authoritative,
                "listed": observation.listed,
                "error_type": observation.error_type,
            },
            sort_keys=True,
        )[:1000],
    )
    evidence.evidence_id = evidence_id_for(enrichment_id, observation.source_url, decision.evidence_key)
    return evidence


def _existing_evidence(sheet_client: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    return {
        str(row.get("evidence_id") or "").strip(): (row_number, row)
        for row_number, row in _records(sheet_client, "Enrichment_Evidence")
        if str(row.get("evidence_id") or "").strip()
    }


def _write_evidence(
    sheet_client: Any,
    evidence: EnrichmentEvidence,
    existing: dict[str, tuple[int, dict[str, Any]]],
) -> bool:
    if evidence.evidence_id in existing:
        return False
    record = evidence.to_dict()
    sheet_client.append_record("Enrichment_Evidence", record)
    next_row = max((row_number for row_number, _ in existing.values()), default=1) + 1
    existing[evidence.evidence_id] = (next_row, record)
    return True


def lifecycle_health_metrics(
    jobs: Iterable[JobPosting],
    queue_items: Iterable[EnrichmentQueueItem],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    job_rows = list(jobs)
    queue_rows = list(queue_items)
    current = _parse_timestamp(now or utc_now_iso()) or datetime.now(UTC)
    pending = [item for item in queue_rows if item.status in {"pending", "in_progress", "retryable_failure"}]
    attempted = [item for item in queue_rows if item.attempt_count > 0]
    successful = [item for item in attempted if item.status in {"enriched", "partial"}]
    created_dates = [_parse_timestamp(item.created_at) for item in pending]
    created_dates = [value for value in created_dates if value is not None]
    oldest_pending_days = max(((current - value).days for value in created_dates), default=0)
    average_attempts = round(sum(item.attempt_count for item in attempted) / len(attempted), 2) if attempted else 0.0
    success_rate = round(100 * len(successful) / len(attempted), 1) if attempted else 0.0
    return {
        "open_verified_jobs": sum(1 for job in job_rows if job.status in {"open", "reopened"} and job.score_status == "verified"),
        "open_provisional_jobs": sum(1 for job in job_rows if job.status in {"open", "reopened"} and job.score_status in {"provisional", "partially_verified"}),
        "enrichment_backlog": len(pending),
        "retryable_failures": sum(1 for item in queue_rows if item.status == "retryable_failure"),
        "ambiguous_matches": sum(1 for item in queue_rows if item.status == "ambiguous"),
        "jobs_likely_closed": sum(1 for job in job_rows if job.status == "likely_closed"),
        "jobs_confirmed_closed": sum(1 for job in job_rows if job.status in TERMINAL_JOB_STATUSES),
        "oldest_pending_enrichment_days": oldest_pending_days,
        "average_enrichment_attempts": average_attempts,
        "enrichment_success_rate_percent": success_rate,
    }


def build_lifecycle_run_record(summary: LifecycleRunSummary, *, started_at: str, finished_at: str) -> dict[str, Any]:
    run_timestamp = finished_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    return {
        "run_id": f"sprint31_lifecycle_{run_timestamp}",
        "run_type": "sprint_31_enrichment_lifecycle",
        "source_type": "jobs",
        "source_name": "Jobs and Enrichment_Queue",
        "status": "success",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": 0,
        "records_found": summary.jobs_evaluated,
        "records_inserted": summary.evidence_written,
        "records_updated": summary.jobs_updated,
        "records_failed": summary.temporary_failures,
        "rows_read": summary.jobs_evaluated,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary.to_dict(), sort_keys=True),
        "created_at": finished_at,
        "updated_at": finished_at,
    }


def run_lifecycle_checks(
    sheet_client: Any,
    *,
    checker: Callable[..., LifecycleObservation] | None = None,
    now: str | None = None,
    limit: int = 50,
    job_key: str = "",
    policy: LifecyclePolicy | None = None,
    write_run_record: bool = True,
) -> LifecycleRunSummary:
    timestamp = now or utc_now_iso()
    lifecycle_checker = checker or DirectUrlLifecycleChecker()
    rules = policy or LifecyclePolicy()
    summary = LifecycleRunSummary()
    job_rows = [(row, job) for row, job in _jobs(sheet_client) if not job_key or job.job_key == job_key]
    queue_rows = [(row, EnrichmentQueueItem.from_dict(record)) for row, record in _records(sheet_client, "Enrichment_Queue")]
    queue_by_job = {item.job_key: (row, item) for row, item in queue_rows if item.job_key}
    evidence_by_id = _existing_evidence(sheet_client)
    due_jobs = [(row, job) for row, job in job_rows if _is_due(job, timestamp)]
    due_jobs.sort(key=lambda pair: (pair[1].lifecycle_next_check_at or "", -pair[1].potential_priority_score, pair[1].job_key))
    summary.jobs_evaluated = len(job_rows)

    for row_number, job in due_jobs[: max(0, limit)]:
        summary.jobs_checked += 1
        observation = lifecycle_checker(job, checked_at=timestamp)
        duplicate = observation.evidence_key == job.lifecycle_last_evidence_key
        decision = apply_lifecycle_observation(job, observation, policy=rules)
        if duplicate:
            summary.duplicate_observations += 1
            summary.jobs_unchanged += 1
            continue

        evidence = _lifecycle_evidence(job, observation, decision)
        summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
        _update_job(sheet_client, row_number, job)
        summary.jobs_updated += 1
        summary.temporary_failures += int(decision.evidence_type == "temporary_failure")
        summary.likely_closed += int(job.status == "likely_closed")
        summary.confirmed_closed += int(job.status in {"confirmed_closed", "closed"})
        summary.expired += int(job.status == "expired")
        summary.reopened += int(job.status == "reopened")
        summary.open_confirmed += int(job.status == "open" and decision.evidence_type == "authoritative_open")

        queue_match = queue_by_job.get(job.job_key)
        if queue_match is not None:
            queue_row, item = queue_match
            queue_changed = False
            if job.status in TERMINAL_JOB_STATUSES:
                queue_changed = item.status != "closed" or bool(item.next_attempt_at)
                item.status = "closed"
                item.next_attempt_at = ""
                item.error_type = "posting_closed"
                item.error_message = job.lifecycle_reason[:1000]
                item.updated_at = timestamp
            elif job.status == "reopened" and item.status == "closed":
                item.status = "pending"
                item.current_stage = "direct_url"
                item.next_attempt_at = ""
                item.error_type = ""
                item.error_message = ""
                item.updated_at = timestamp
                queue_changed = True
            elif item.status in QUEUE_RETRY_STATUSES:
                queue_changed = schedule_enrichment_retry(item, now=timestamp, policy=rules)
                if item.status == "permanent_failure":
                    summary.queue_permanent_failures += int(queue_changed)
                else:
                    summary.queue_retries_scheduled += int(queue_changed)
            if queue_changed:
                sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())

    summary.health_metrics = lifecycle_health_metrics(
        [job for _, job in job_rows],
        [item for _, item in queue_rows],
        now=timestamp,
    )
    if write_run_record and hasattr(sheet_client, "append_run"):
        sheet_client.append_run(build_lifecycle_run_record(summary, started_at=timestamp, finished_at=timestamp))
    return summary


def preview_lifecycle_checks(sheet_client: Any, *, now: str | None = None, job_key: str = "") -> dict[str, Any]:
    timestamp = now or utc_now_iso()
    jobs = [job for _, job in _jobs(sheet_client) if (not job_key or job.job_key == job_key) and _is_due(job, timestamp)]
    return {
        "due_jobs": len(jobs),
        "jobs": [
            {
                "job_key": job.job_key,
                "company": job.company,
                "title": job.title,
                "status": job.status,
                "lifecycle_next_check_at": job.lifecycle_next_check_at,
                "url": job.enrichment_source_url or job.canonical_url,
            }
            for job in jobs
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check job posting lifecycle, schedule retries, and record closure evidence")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--job-key", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.run and not args.dry_run:
        raise SystemExit("Choose --run or --dry-run")
    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        print(json.dumps(preview_lifecycle_checks(sheet_client, job_key=args.job_key), indent=2))
        return
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    summary = run_lifecycle_checks(sheet_client, limit=args.limit, job_key=args.job_key)
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
