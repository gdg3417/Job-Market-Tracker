from __future__ import annotations

from typing import Any

from src.enrichment.matcher import is_authoritative_url, locations_compatible
from src.enrichment.models import EnrichmentEvidence
from src.models import JobPosting, utc_now_iso

UNKNOWN_VALUES = {"", "unknown", "unspecified", "not specified", "n/a", "na", "none"}


def _unknown(value: Any) -> bool:
    return str(value or "").strip().lower() in UNKNOWN_VALUES


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _set_if_changed(job: JobPosting, field_name: str, value: Any, changed: list[str]) -> None:
    if value in (None, ""):
        return
    if getattr(job, field_name) != value:
        setattr(job, field_name, value)
        changed.append(field_name)


def merge_verified_evidence(
    job: JobPosting,
    evidence: EnrichmentEvidence,
    *,
    match_confidence: int,
    evidence_rules: dict[str, Any] | None = None,
    completed_at: str | None = None,
) -> tuple[JobPosting, list[str]]:
    """Merge direct employer or ATS evidence into one existing job without changing its identity."""
    if match_confidence < 80:
        raise ValueError("Evidence cannot be merged below the automatic match threshold")

    changed: list[str] = []
    completed_at = completed_at or utc_now_iso()

    if evidence.description_text:
        current_description = str(job.description_text or "")
        incoming_description = str(evidence.description_text or "")
        current_is_generic = current_description.lower().startswith("extracted from gmail job alert")
        if current_is_generic or len(incoming_description) > len(current_description):
            _set_if_changed(job, "description_text", incoming_description, changed)

    if evidence.source_location and (_unknown(job.location) or locations_compatible(job.location, evidence.source_location)):
        _set_if_changed(job, "location", evidence.source_location, changed)

    incoming_salary_present = evidence.salary_min is not None or evidence.salary_max is not None
    if evidence.salary_min is not None and job.salary_min is None:
        _set_if_changed(job, "salary_min", evidence.salary_min, changed)
    if evidence.salary_max is not None and job.salary_max is None:
        _set_if_changed(job, "salary_max", evidence.salary_max, changed)
    if evidence.currency and incoming_salary_present and _unknown(job.currency):
        _set_if_changed(job, "currency", evidence.currency, changed)

    if not _unknown(evidence.remote_status):
        _set_if_changed(job, "remote_status", evidence.remote_status, changed)
    if not _unknown(evidence.work_model):
        _set_if_changed(job, "work_model", evidence.work_model, changed)

    authoritative_url = evidence.canonical_url or evidence.source_url
    if is_authoritative_url(authoritative_url):
        _set_if_changed(job, "canonical_url", authoritative_url, changed)

    job.enrichment_priority = job.potential_priority if job.potential_priority in {"high", "medium", "low"} else job.enrichment_priority
    job.enrichment_last_attempted_at = completed_at
    job.enrichment_completed_at = completed_at
    job.enrichment_source_url = evidence.source_url or authoritative_url
    job.enrichment_match_confidence = match_confidence

    if evidence_rules:
        from src.potential_priority import calculate_evidence_completeness

        evidence_score, _ = calculate_evidence_completeness(job, evidence_rules)
        job.evidence_completeness_score = max(job.evidence_completeness_score, evidence_score)
        enrichment_rules = evidence_rules.get("enrichment", {}) or {}
        complete_threshold = _safe_int(enrichment_rules.get("complete_evidence_threshold"), 70)
        partial_threshold = _safe_int(enrichment_rules.get("partial_evidence_threshold"), 40)
        job.enrichment_status = "enriched" if job.evidence_completeness_score >= complete_threshold else "partial"
        if job.score_status not in {"excluded", "verified"}:
            job.score_status = "partially_verified" if job.evidence_completeness_score >= partial_threshold else "provisional"
    else:
        job.enrichment_status = "enriched" if evidence.description_text else "partial"
        if job.score_status not in {"excluded", "verified"} and evidence.description_text:
            job.score_status = "partially_verified"

    if hasattr(job, "refresh_updated_at"):
        job.refresh_updated_at()
    return job, changed
