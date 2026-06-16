from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz

from src.models import JobPosting
from src.normalize import normalize_key_part


def source_signature(job: JobPosting) -> str:
    if not job.source_primary or not job.source_job_id:
        return ""
    return f"{normalize_key_part(job.source_primary)}:{normalize_key_part(job.source_job_id)}"


def fuzzy_identity(job: JobPosting) -> str:
    return " ".join([normalize_key_part(job.company), normalize_key_part(job.title), normalize_key_part(job.location)])


def find_duplicate(new_job: JobPosting, existing_jobs: Iterable[JobPosting], threshold: int = 92) -> JobPosting | None:
    new_source_signature = source_signature(new_job)
    for existing in existing_jobs:
        if new_source_signature and new_source_signature == source_signature(existing):
            return existing
        if new_job.canonical_url and new_job.canonical_url == existing.canonical_url:
            return existing
        if new_job.job_key and new_job.job_key == existing.job_key:
            return existing
    new_identity = fuzzy_identity(new_job)
    for existing in existing_jobs:
        if fuzz.token_set_ratio(new_identity, fuzzy_identity(existing)) >= threshold:
            return existing
    return None


def is_duplicate(new_job: JobPosting, existing_jobs: Iterable[JobPosting], threshold: int = 92) -> bool:
    return find_duplicate(new_job, existing_jobs, threshold=threshold) is not None
