from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from rapidfuzz import fuzz

from src.models import JobPosting, today_iso, utc_now_iso
from src.normalize import build_job_key, normalize_key_part, normalize_url

SOURCE_FIELDS = [
    "source_key",
    "job_key",
    "company",
    "title",
    "source_primary",
    "source_type",
    "source_job_id",
    "canonical_url",
    "source_url",
    "first_seen_date",
    "last_seen_date",
    "status",
    "created_at",
    "updated_at",
]

SCORE_FIELDS = {
    "fit_score",
    "p_and_l_path_score",
    "growth_ownership_score",
    "executive_exposure_score",
    "operating_cadence_score",
    "comp_score",
    "location_score",
    "industry_match_score",
    "total_score",
    "alert_tier",
    "score_explanation",
}

POTENTIAL_FIELDS = {
    "potential_priority_score",
    "potential_priority",
    "potential_priority_reason",
}

JOB_OVERWRITE_FIELDS = [
    "company",
    "title",
    "location",
    "remote_status",
    "work_model",
    "commute_estimate_minutes",
    "salary_min",
    "salary_max",
    "currency",
    "total_comp_estimate",
    "description_text",
    "role_family",
    "role_level",
    *sorted(SCORE_FIELDS),
    *sorted(POTENTIAL_FIELDS),
]

SOURCE_PRIMARY_FIELDS = ["source_primary", "source_type", "source", "ats_platform"]
SOURCE_JOB_ID_FIELDS = ["source_job_id", "job_id", "posting_id", "requisition_id"]
SOURCE_URL_FIELDS = ["canonical_url", "source_url", "url", "hosted_url", "absolute_url"]
SCORE_STATUS_RANK = {"provisional": 1, "partially_verified": 2, "verified": 3, "excluded": 4}
ENRICHMENT_STATUS_RANK = {
    "not_required": 0,
    "pending": 1,
    "in_progress": 2,
    "not_found": 2,
    "retryable_failure": 2,
    "partial": 3,
    "ambiguous": 3,
    "enriched": 4,
    "permanent_failure": 4,
    "closed": 5,
}


@dataclass(slots=True)
class UpsertSummary:
    records_seen: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    job_sources_created: int = 0
    job_sources_updated: int = 0
    duplicates_matched: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class SourceRowMatch:
    row_number: int
    record: dict[str, Any]


def _header_key(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _row_get(row: dict[str, Any], *candidate_names: str) -> Any:
    for name in candidate_names:
        if name in row:
            return row.get(name)
    normalized_row = {_header_key(key): value for key, value in row.items()}
    for name in candidate_names:
        value = normalized_row.get(_header_key(name))
        if value not in (None, ""):
            return value
    return ""


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _stable_hash(value: str, prefix: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def canonical_url_key(value: Any) -> str:
    return normalize_url(value)


def source_signature_from_parts(source_primary: Any, source_job_id: Any) -> str:
    source = normalize_key_part(source_primary)
    job_id = normalize_key_part(source_job_id)
    if not source or not job_id:
        return ""
    return f"{source}:{job_id}"


def source_signature(job: JobPosting) -> str:
    return source_signature_from_parts(job.source_primary, job.source_job_id)


def source_signature_from_row(row: dict[str, Any]) -> str:
    return source_signature_from_parts(
        _row_get(row, *SOURCE_PRIMARY_FIELDS),
        _row_get(row, *SOURCE_JOB_ID_FIELDS),
    )


def source_url_from_row(row: dict[str, Any]) -> str:
    return canonical_url_key(_row_get(row, *SOURCE_URL_FIELDS))


def source_primary_from_row(row: dict[str, Any]) -> str:
    return normalize_key_part(_row_get(row, *SOURCE_PRIMARY_FIELDS))


def job_key_from_row(row: dict[str, Any]) -> str:
    return str(_row_get(row, "job_key", "job key")).strip()


def fuzzy_identity(job: JobPosting) -> str:
    return " ".join(
        [
            normalize_key_part(job.company),
            normalize_key_part(job.title),
            normalize_key_part(job.location),
        ]
    ).strip()


def title_company_identity(job: JobPosting) -> str:
    return " ".join([normalize_key_part(job.company), normalize_key_part(job.title)]).strip()


def ensure_job_key(job: JobPosting) -> JobPosting:
    if not job.job_key:
        job.job_key = build_job_key(job.company, job.title, job.location)
    return job


def build_source_key(job: JobPosting) -> str:
    ensure_job_key(job)
    signature = source_signature(job)
    url = canonical_url_key(job.canonical_url)
    source = normalize_key_part(job.source_primary) or "unknown"
    if signature:
        base = signature
    elif url:
        base = f"{source}:{url}"
    else:
        base = f"{source}:{job.job_key}"
    return _stable_hash(base, "src")


def build_job_source_record(job: JobPosting, seen_date: str | None = None) -> dict[str, Any]:
    ensure_job_key(job)
    current_date = seen_date or job.last_seen_date or today_iso()
    current_timestamp = utc_now_iso()
    first_seen = job.first_seen_date or current_date
    source_url = canonical_url_key(job.canonical_url)
    return {
        "source_key": build_source_key(job),
        "job_key": job.job_key,
        "company": job.company,
        "title": job.title,
        "source_primary": job.source_primary,
        "source_type": job.source_primary,
        "source_job_id": job.source_job_id,
        "canonical_url": source_url,
        "source_url": source_url,
        "first_seen_date": first_seen,
        "last_seen_date": current_date,
        "status": "active",
        "created_at": current_timestamp,
        "updated_at": current_timestamp,
    }


def merge_source_record(existing: dict[str, Any], incoming: dict[str, Any], seen_date: str | None = None) -> dict[str, Any]:
    merged = dict(existing)
    for field_name in SOURCE_FIELDS:
        if field_name in {"first_seen_date", "created_at"}:
            continue
        incoming_value = incoming.get(field_name)
        if _has_value(incoming_value):
            merged[field_name] = incoming_value
    merged["first_seen_date"] = _row_get(existing, "first_seen_date", "first seen date") or incoming.get("first_seen_date", "")
    merged["created_at"] = _row_get(existing, "created_at", "created at") or incoming.get("created_at", "")
    merged["last_seen_date"] = seen_date or incoming.get("last_seen_date") or today_iso()
    merged["updated_at"] = utc_now_iso()
    return merged


def find_source_row_match(
    job: JobPosting,
    existing_sources: Sequence[tuple[int, dict[str, Any]]],
) -> SourceRowMatch | None:
    source_key = build_source_key(job)
    new_signature = source_signature(job)
    new_source = normalize_key_part(job.source_primary)
    new_url = canonical_url_key(job.canonical_url)

    for row_number, row in existing_sources:
        if source_key and _row_get(row, "source_key", "source key") == source_key:
            return SourceRowMatch(row_number=row_number, record=row)
    if new_signature:
        for row_number, row in existing_sources:
            if new_signature == source_signature_from_row(row):
                return SourceRowMatch(row_number=row_number, record=row)
    if new_source and new_url:
        for row_number, row in existing_sources:
            if new_source == source_primary_from_row(row) and new_url == source_url_from_row(row):
                return SourceRowMatch(row_number=row_number, record=row)
    return None


def find_duplicate(
    new_job: JobPosting,
    existing_jobs: Iterable[JobPosting],
    existing_sources: Iterable[dict[str, Any]] | None = None,
    threshold: int = 92,
    description_threshold: int = 96,
) -> JobPosting | None:
    existing_job_list = [ensure_job_key(job) for job in existing_jobs]
    job_by_key = {job.job_key: job for job in existing_job_list if job.job_key}
    source_rows = list(existing_sources or [])
    ensure_job_key(new_job)

    new_source_signature = source_signature(new_job)
    if new_source_signature:
        for row in source_rows:
            if new_source_signature == source_signature_from_row(row):
                matched_job = job_by_key.get(job_key_from_row(row))
                if matched_job is not None:
                    return matched_job
        for existing in existing_job_list:
            if new_source_signature == source_signature(existing):
                return existing

    new_url = canonical_url_key(new_job.canonical_url)
    if new_url:
        for row in source_rows:
            if new_url == source_url_from_row(row):
                matched_job = job_by_key.get(job_key_from_row(row))
                if matched_job is not None:
                    return matched_job
        for existing in existing_job_list:
            if new_url == canonical_url_key(existing.canonical_url):
                return existing

    if new_job.job_key:
        matched_job = job_by_key.get(new_job.job_key)
        if matched_job is not None:
            return matched_job

    new_identity = fuzzy_identity(new_job)
    if new_identity:
        for existing in existing_job_list:
            if fuzz.token_set_ratio(new_identity, fuzzy_identity(existing)) >= threshold:
                return existing

    new_title_company = title_company_identity(new_job)
    new_description = normalize_key_part(new_job.description_text)
    if new_title_company and new_description:
        for existing in existing_job_list:
            title_company_score = fuzz.token_set_ratio(new_title_company, title_company_identity(existing))
            description_score = fuzz.token_set_ratio(new_description, normalize_key_part(existing.description_text))
            if title_company_score >= threshold and description_score >= description_threshold:
                return existing
    return None


def is_duplicate(
    new_job: JobPosting,
    existing_jobs: Iterable[JobPosting],
    existing_sources: Iterable[dict[str, Any]] | None = None,
    threshold: int = 92,
) -> bool:
    return find_duplicate(new_job, existing_jobs, existing_sources=existing_sources, threshold=threshold) is not None


def _merge_priority_and_evidence(merged: JobPosting, incoming: JobPosting) -> None:
    for field_name in POTENTIAL_FIELDS:
        incoming_value = getattr(incoming, field_name)
        if _has_value(incoming_value):
            setattr(merged, field_name, incoming_value)

    existing_evidence = merged.evidence_completeness_score
    incoming_evidence = incoming.evidence_completeness_score
    existing_rank = SCORE_STATUS_RANK.get(merged.score_status, 0)
    incoming_rank = SCORE_STATUS_RANK.get(incoming.score_status, 0)
    if incoming_rank > existing_rank or (incoming_rank == existing_rank and incoming_evidence >= existing_evidence):
        merged.score_status = incoming.score_status
        merged.evidence_completeness_score = incoming_evidence
        if incoming.score_status in {"verified", "excluded"}:
            merged.verified_total_score = incoming.verified_total_score
            merged.verified_alert_tier = incoming.verified_alert_tier
    elif incoming_evidence > existing_evidence:
        merged.evidence_completeness_score = incoming_evidence

    existing_enrichment_rank = ENRICHMENT_STATUS_RANK.get(merged.enrichment_status, 0)
    incoming_enrichment_rank = ENRICHMENT_STATUS_RANK.get(incoming.enrichment_status, 0)
    if incoming_enrichment_rank >= existing_enrichment_rank:
        for field_name in [
            "enrichment_status",
            "enrichment_priority",
            "enrichment_last_attempted_at",
            "enrichment_completed_at",
            "enrichment_source_url",
            "enrichment_match_confidence",
        ]:
            incoming_value = getattr(incoming, field_name)
            if _has_value(incoming_value) or field_name in {"enrichment_status", "enrichment_priority"}:
                setattr(merged, field_name, incoming_value)


def merge_job(existing: JobPosting, incoming: JobPosting, seen_date: str | None = None) -> JobPosting:
    ensure_job_key(existing)
    ensure_job_key(incoming)
    merged = JobPosting.from_dict(existing.to_dict())

    incoming_is_scored = incoming.total_score > 0 or incoming.alert_tier != "unscored"
    protect_verified_score = existing.score_status == "verified" and incoming.score_status != "verified"
    for field_name in JOB_OVERWRITE_FIELDS:
        if field_name in POTENTIAL_FIELDS:
            continue
        if field_name in SCORE_FIELDS and (not incoming_is_scored or protect_verified_score):
            continue
        incoming_value = getattr(incoming, field_name)
        if _has_value(incoming_value):
            setattr(merged, field_name, incoming_value)

    _merge_priority_and_evidence(merged, incoming)

    for field_name in ["source_primary", "source_job_id", "canonical_url"]:
        if not _has_value(getattr(merged, field_name)) and _has_value(getattr(incoming, field_name)):
            setattr(merged, field_name, getattr(incoming, field_name))

    merged.job_key = existing.job_key
    merged.first_seen_date = existing.first_seen_date or incoming.first_seen_date or seen_date or today_iso()
    merged.created_at = existing.created_at or incoming.created_at or utc_now_iso()
    merged.mark_seen(seen_date)
    return merged


def _read_records_with_row_numbers(sheet_client: Any, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet_name))
    records = sheet_client.read_records(worksheet_name)
    return [(index + 2, record) for index, record in enumerate(records)]


def _nonblank_job_record(record: dict[str, Any]) -> bool:
    return any(_row_get(record, "job_key", "company", "title", "canonical_url", "source_job_id"))


def _load_existing_jobs(sheet_client: Any) -> tuple[list[JobPosting], dict[str, int]]:
    rows = _read_records_with_row_numbers(sheet_client, "Jobs")
    existing_jobs: list[JobPosting] = []
    row_by_job_key: dict[str, int] = {}
    for row_number, record in rows:
        if not _nonblank_job_record(record):
            continue
        job = ensure_job_key(JobPosting.from_dict(record))
        existing_jobs.append(job)
        row_by_job_key[job.job_key] = row_number
    return existing_jobs, row_by_job_key


def _upsert_source_record(
    sheet_client: Any,
    job: JobPosting,
    existing_source_rows: list[tuple[int, dict[str, Any]]],
    summary: UpsertSummary,
    seen_date: str | None = None,
) -> None:
    source_record = build_job_source_record(job, seen_date=seen_date)
    match = find_source_row_match(job, existing_source_rows)
    if match is None:
        sheet_client.append_job_source(source_record)
        existing_source_rows.append((0, source_record))
        summary.job_sources_created += 1
        return

    merged_record = merge_source_record(match.record, source_record, seen_date=seen_date)
    sheet_client.update_job_source(match.row_number, merged_record)
    for index, (row_number, _) in enumerate(existing_source_rows):
        if row_number == match.row_number:
            existing_source_rows[index] = (row_number, merged_record)
            break
    summary.job_sources_updated += 1


def upsert_jobs(
    sheet_client: Any,
    incoming_jobs: Iterable[JobPosting],
    *,
    seen_date: str | None = None,
    threshold: int = 92,
) -> UpsertSummary:
    current_date = seen_date or today_iso()
    summary = UpsertSummary()
    existing_jobs, row_by_job_key = _load_existing_jobs(sheet_client)
    existing_source_rows = _read_records_with_row_numbers(sheet_client, "Job_Sources")

    for incoming_job in incoming_jobs:
        summary.records_seen += 1
        incoming_job = ensure_job_key(incoming_job)
        incoming_job.last_seen_date = current_date
        if not incoming_job.first_seen_date:
            incoming_job.first_seen_date = current_date

        source_rows_only = [row for _, row in existing_source_rows]
        matched_job = find_duplicate(
            incoming_job,
            existing_jobs,
            existing_sources=source_rows_only,
            threshold=threshold,
        )

        if matched_job is None:
            incoming_job.mark_seen(current_date)
            sheet_client.append_job(incoming_job)
            existing_jobs.append(incoming_job)
            row_by_job_key.setdefault(incoming_job.job_key, 0)
            _upsert_source_record(sheet_client, incoming_job, existing_source_rows, summary, seen_date=current_date)
            summary.jobs_created += 1
            continue

        summary.duplicates_matched += 1
        target_key = matched_job.job_key
        incoming_for_source = JobPosting.from_dict(incoming_job.to_dict())
        incoming_for_source.job_key = target_key
        _upsert_source_record(sheet_client, incoming_for_source, existing_source_rows, summary, seen_date=current_date)

        row_number = row_by_job_key.get(target_key)
        if row_number:
            merged_job = merge_job(matched_job, incoming_job, seen_date=current_date)
            sheet_client.update_job(row_number, merged_job)
            for index, job in enumerate(existing_jobs):
                if job.job_key == target_key:
                    existing_jobs[index] = merged_job
                    break
            summary.jobs_updated += 1

    return summary
