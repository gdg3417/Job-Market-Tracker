from __future__ import annotations

from datetime import datetime
from typing import Any

from src.verification_health_models import Blocker, HealthThresholds, identity, row_timestamp, safe_int, truthy
from src.verification_health_state import authoritative, is_deferred


def classify_blocker(
    job: dict[str, Any],
    queue_row: dict[str, Any] | None,
    evidence_rows: list[dict[str, Any]],
    *,
    as_of: datetime,
    thresholds: HealthThresholds,
) -> Blocker:
    queue = queue_row or {}
    status = identity(queue.get("status") or job.get("enrichment_status"))
    error_type = identity(queue.get("error_type"))
    error_message = str(queue.get("error_message") or job.get("enrichment_error_message") or "").strip()
    error_text = f"{error_type} {identity(error_message)}"
    attempts = safe_int(queue.get("attempt_count"), safe_int(job.get("enrichment_attempt_count"), 0))
    confidence = safe_int(queue.get("match_confidence") or job.get("enrichment_match_confidence"), 0)

    if is_deferred(job, as_of):
        return Blocker("manual_review_required", "Manually deferred")
    if status == "retryable failure" or row_timestamp(queue, "next_attempt_at"):
        return Blocker("retry_scheduled", error_message or "Retry is scheduled")
    if "blocked" in error_text or "forbidden" in error_text or "unauthorized" in error_text:
        return Blocker("source_blocked", error_message)
    if "timeout" in error_text or "timed out" in error_text:
        return Blocker("source_timeout", error_message)
    if "parser" in error_text or "parse" in error_text:
        return Blocker("parser_failure", error_message)
    if status == "ambiguous" or (confidence and confidence < thresholds.authoritative_match_min_confidence):
        return Blocker("authoritative_match_below_threshold", f"match_confidence={confidence}")
    if status == "not found" or "not found" in error_text or "404" in error_text:
        return Blocker("source_not_found", error_message)
    if status in {"manual review", "manual review required"}:
        return Blocker("manual_review_required", error_message)
    if status in {"permanent failure", "closed"}:
        return Blocker("no_supported_enrichment_path", error_message)
    if attempts <= 0 and not row_timestamp(job, "enrichment_last_attempted_at"):
        return Blocker("enrichment_not_attempted", "No successful or failed attempt is recorded")
    if not authoritative(job, queue, thresholds):
        return Blocker("no_authoritative_url", "No accepted employer or ATS URL")

    accepted = [row for row in evidence_rows if truthy(row.get("accepted"))]
    description = str(job.get("description_text") or "").strip()
    if not description and not any(str(row.get("description_text") or "").strip() for row in accepted):
        return Blocker("missing_description", "Description evidence is absent")
    if not str(job.get("location") or "").strip():
        return Blocker("missing_location", "Location evidence is absent")
    if job.get("salary_min") in (None, "") and job.get("salary_max") in (None, ""):
        return Blocker("missing_compensation", "Compensation evidence is absent")
    if identity(job.get("work_model")) in {"", "unknown"} and identity(job.get("remote_status")) in {"", "unknown"}:
        return Blocker("missing_work_model", "Work-model evidence is absent")
    return Blocker("other", error_message or "Verification remains incomplete")
