from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.data_quality import append_rejected_jobs, filter_jobs_for_upsert
from src.dedupe import (
    UpsertSummary,
    build_job_source_record,
    ensure_job_key,
    find_duplicate,
    find_source_row_match,
    merge_job,
    merge_source_record,
)
from src.models import JobPosting, today_iso


@dataclass(slots=True)
class JobUpsertState:
    existing_jobs: list[JobPosting]
    row_by_job_key: dict[str, int]
    existing_source_rows: list[tuple[int, dict[str, Any]]]


def _read_records_with_row_numbers(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    records = sheet_client.read_records(worksheet_name)
    return [(index + 2, record) for index, record in enumerate(records)]


def _next_row_number(existing_rows: list[tuple[int, dict[str, Any]]]) -> int:
    row_numbers = [row_number for row_number, _ in existing_rows if row_number and row_number > 1]
    return (max(row_numbers) if row_numbers else 1) + 1


def _next_row_number_from_map(row_by_key: dict[str, int]) -> int:
    row_numbers = [row_number for row_number in row_by_key.values() if row_number and row_number > 1]
    return (max(row_numbers) if row_numbers else 1) + 1


def _record_has_any_value(record: dict[str, Any], field_names: list[str]) -> bool:
    return any(str(record.get(field_name, "")).strip() for field_name in field_names)


def _load_existing_jobs(sheet_client: Any) -> tuple[list[JobPosting], dict[str, int]]:
    rows = _read_records_with_row_numbers(sheet_client, "Jobs")
    existing_jobs: list[JobPosting] = []
    row_by_job_key: dict[str, int] = {}
    for row_number, record in rows:
        if not _record_has_any_value(record, ["job_key", "company", "title", "canonical_url", "source_job_id"]):
            continue
        job = ensure_job_key(JobPosting.from_dict(record))
        existing_jobs.append(job)
        row_by_job_key[job.job_key] = row_number
    return existing_jobs, row_by_job_key


def load_job_upsert_state(sheet_client: Any) -> JobUpsertState:
    existing_jobs, row_by_job_key = _load_existing_jobs(sheet_client)
    existing_source_rows = _read_records_with_row_numbers(sheet_client, "Job_Sources")
    return JobUpsertState(
        existing_jobs=existing_jobs,
        row_by_job_key=row_by_job_key,
        existing_source_rows=existing_source_rows,
    )


def _job_upsert_state(sheet_client: Any) -> JobUpsertState:
    cached = getattr(sheet_client, "_job_upsert_state", None)
    if isinstance(cached, JobUpsertState):
        return cached

    state = load_job_upsert_state(sheet_client)
    try:
        setattr(sheet_client, "_job_upsert_state", state)
    except (AttributeError, TypeError):
        pass
    return state


def _replace_existing_job(existing_jobs: list[JobPosting], replacement: JobPosting) -> None:
    for index, job in enumerate(existing_jobs):
        if job.job_key == replacement.job_key:
            existing_jobs[index] = replacement
            return
    existing_jobs.append(replacement)


def _replace_existing_source(
    existing_source_rows: list[tuple[int, dict[str, Any]]],
    row_number: int,
    replacement: dict[str, Any],
) -> None:
    for index, (existing_row_number, _) in enumerate(existing_source_rows):
        if existing_row_number == row_number:
            existing_source_rows[index] = (existing_row_number, replacement)
            return
    existing_source_rows.append((row_number, replacement))


def _upsert_source_record(
    sheet_client: Any,
    job: JobPosting,
    existing_source_rows: list[tuple[int, dict[str, Any]]],
    summary: UpsertSummary,
    seen_date: str,
) -> None:
    source_record = build_job_source_record(job, seen_date=seen_date)
    match = find_source_row_match(job, existing_source_rows)
    if match is None:
        next_row_number = _next_row_number(existing_source_rows)
        sheet_client.append_job_source(source_record)
        existing_source_rows.append((next_row_number, source_record))
        summary.job_sources_created += 1
        return

    merged_record = merge_source_record(match.record, source_record, seen_date=seen_date)
    if match.row_number > 1:
        sheet_client.update_job_source(match.row_number, merged_record)
    _replace_existing_source(existing_source_rows, match.row_number, merged_record)
    summary.job_sources_updated += 1


def upsert_jobs(
    sheet_client: Any,
    incoming_jobs: Iterable[JobPosting],
    *,
    seen_date: str | None = None,
    threshold: int = 92,
    state: JobUpsertState | None = None,
) -> UpsertSummary:
    current_date = seen_date or today_iso()
    summary = UpsertSummary()
    incoming_jobs_list = list(incoming_jobs)
    summary.records_seen = len(incoming_jobs_list)

    accepted_jobs, rejected_jobs = filter_jobs_for_upsert(incoming_jobs_list)
    append_rejected_jobs(sheet_client, rejected_jobs)
    if not accepted_jobs:
        return summary

    working_state = state or _job_upsert_state(sheet_client)
    existing_jobs = working_state.existing_jobs
    row_by_job_key = working_state.row_by_job_key
    existing_source_rows = working_state.existing_source_rows

    for incoming_job in accepted_jobs:
        incoming_job = ensure_job_key(incoming_job)
        incoming_job.last_seen_date = current_date
        if not incoming_job.first_seen_date:
            incoming_job.first_seen_date = current_date

        matched_job = find_duplicate(
            incoming_job,
            existing_jobs,
            existing_sources=[row for _, row in existing_source_rows],
            threshold=threshold,
        )

        if matched_job is None:
            incoming_job.mark_seen(current_date)
            next_job_row = _next_row_number_from_map(row_by_job_key)
            sheet_client.append_job(incoming_job)
            existing_jobs.append(incoming_job)
            row_by_job_key[incoming_job.job_key] = next_job_row
            _upsert_source_record(sheet_client, incoming_job, existing_source_rows, summary, current_date)
            summary.jobs_created += 1
            continue

        summary.duplicates_matched += 1
        target_key = matched_job.job_key
        incoming_for_source = JobPosting.from_dict(incoming_job.to_dict())
        incoming_for_source.job_key = target_key
        _upsert_source_record(sheet_client, incoming_for_source, existing_source_rows, summary, current_date)

        row_number = row_by_job_key.get(target_key)
        if row_number:
            merged_job = merge_job(matched_job, incoming_job, seen_date=current_date)
            sheet_client.update_job(row_number, merged_job)
            _replace_existing_job(existing_jobs, merged_job)
            summary.jobs_updated += 1

    return summary
