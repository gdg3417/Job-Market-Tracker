from __future__ import annotations

from datetime import datetime
from typing import Any

from src.verification_health_models import HealthComponent, HealthThresholds, age_hours, identity, row_timestamp, safe_int, truthy
from src.verification_health_state import (
    authoritative,
    daily_run,
    enrichment_run,
    is_high,
    is_medium_signal,
    is_open,
    is_verified,
    latest_run,
    parse_notes,
)

SEVERITY = {"Healthy": 0, "Watch": 1, "Degraded": 2, "Blocked": 3}


def classification(score: int) -> str:
    if score >= 85:
        return "Healthy"
    if score >= 60:
        return "Watch"
    if score >= 40:
        return "Degraded"
    return "Blocked"


def _inverse_score(rate: float, watch: float, degraded: float) -> int:
    if rate <= 0:
        return 100
    if rate <= watch:
        return round(100 - 15 * rate / max(watch, 0.0001))
    if rate <= degraded:
        return round(85 - 45 * (rate - watch) / max(degraded - watch, 0.0001))
    return max(0, round(40 * (1 - (rate - degraded) / max(1 - degraded, 0.0001))))


def _positive_score(rate: float, watch: float, degraded: float) -> int:
    if rate >= watch:
        return 100
    if rate >= degraded:
        return round(40 + 60 * (rate - degraded) / max(watch - degraded, 0.0001))
    return max(0, round(40 * rate / max(degraded, 0.0001)))


def _component(name: str, label: str, score: int, metrics: dict[str, Any], critical: bool = False) -> HealthComponent:
    normalized = max(0, min(100, int(score)))
    return HealthComponent(name, label, normalized, "Blocked" if critical else classification(normalized), metrics, critical)


def calculate_components(
    jobs: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    queues: dict[str, dict[str, Any]],
    evidence: list[dict[str, Any]],
    breaches: list[dict[str, Any]],
    as_of: datetime,
    thresholds: HealthThresholds,
) -> tuple[list[HealthComponent], list[str]]:
    components: list[HealthComponent] = []
    overrides: list[str] = []
    latest = daily_run(runs)
    latest_age = age_hours(row_timestamp(latest or {}, "finished_at", "started_at"), as_of)
    latest_status = identity((latest or {}).get("status"))
    critical = (
        latest is None
        or latest_status not in {"success", "completed"}
        or latest_age is None
        or latest_age > thresholds.stale_daily_run_hours
    )
    if critical:
        overrides.append("No successful recent daily or enrichment run")
    components.append(_component(
        "workflow_reliability", "Workflow reliability", 0 if critical else 100,
        {
            "latest_run_id": str((latest or {}).get("run_id") or ""),
            "latest_run_type": str((latest or {}).get("run_type") or ""),
            "latest_status": latest_status or "not_logged",
            "latest_age_hours": latest_age,
            "stale_after_hours": thresholds.stale_daily_run_hours,
        },
        critical,
    ))

    operational_notes = parse_notes(latest)
    gmail_run = latest_run(
        runs,
        lambda row: "gmail" in identity(row.get("run_type")) or "gmail" in identity(row.get("source_name")),
    )
    gmail_notes = parse_notes(gmail_run)
    ingestion_source = gmail_run or latest or {}
    ingestion_notes = gmail_notes or operational_notes
    inserted = max(0, safe_int(
        ingestion_notes.get("gmail_messages_newly_processed")
        or ingestion_notes.get("gmail_jobs_accepted")
        or ingestion_source.get("records_inserted"), 0,
    ))
    failed = max(0, safe_int(
        ingestion_notes.get("gmail_messages_failed") or ingestion_source.get("records_failed"), 0,
    ))
    backlog = max(0, safe_int(
        ingestion_notes.get("gmail_backlog_remaining") or ingestion_notes.get("backlog_remaining"), 0,
    ))
    ingestion_total = inserted + failed + backlog
    ingestion_score = round(100 * (1 - (failed + backlog) / max(1, ingestion_total))) if ingestion_total else 50
    components.append(_component(
        "ingestion_health", "Ingestion health", ingestion_score,
        {"records_inserted": inserted, "records_failed": failed, "gmail_backlog": backlog, "status": "measured" if ingestion_total else "not_logged"},
    ))

    latest_enrichment = enrichment_run(runs)
    enrichment_notes = parse_notes(latest_enrichment)
    direct = enrichment_notes.get("direct_link") or {}
    company = enrichment_notes.get("company_ats") or {}
    external = enrichment_notes.get("external_search") or {}
    attempts = sum(safe_int(value, 0) for value in (
        direct.get("direct_attempts"), company.get("company_ats_attempts"), external.get("queries_executed"),
    ))
    failures = sum(safe_int(value, 0) for value in (
        direct.get("retryable_failures"), direct.get("permanent_failures"), company.get("failures"), external.get("search_failures"),
    ))
    source_rate = failures / max(1, attempts)
    source_score = _inverse_score(source_rate, thresholds.source_watch_failure_rate, thresholds.source_degraded_failure_rate) if attempts else 70
    components.append(_component(
        "source_health", "Source health", source_score,
        {
            "latest_enrichment_run_id": str((latest_enrichment or {}).get("run_id") or ""),
            "attempts": attempts,
            "failures": failures,
            "failure_rate": round(source_rate, 4),
            "status": "measured" if attempts else "no_attempts_logged",
        },
    ))

    priority_open = [job for job in jobs if is_open(job) and (is_high(job) or is_medium_signal(job))]
    high_open = [job for job in jobs if is_open(job) and is_high(job)]
    breached_keys = {str(row.get("job_key") or "") for row in breaches}
    high_keys = {str(job.get("job_key") or "") for job in high_open}
    breached_high = high_keys & breached_keys
    breach_rate = len(breached_high) / max(1, len(high_open))
    verified_high = sum(1 for job in high_open if is_verified(job))
    conversion_rate = verified_high / max(1, len(high_open))
    breach_score = _inverse_score(breach_rate, thresholds.verification_watch_breach_rate, thresholds.verification_degraded_breach_rate)
    conversion_score = _positive_score(conversion_rate, thresholds.verification_watch_conversion_rate, thresholds.verification_degraded_conversion_rate)
    verification_score = round(0.60 * breach_score + 0.40 * conversion_score) if high_open else 100
    components.append(_component(
        "verification_health", "Verification health", verification_score,
        {
            "high_potential_open": len(high_open), "high_potential_verified": verified_high,
            "verified_conversion_rate": round(conversion_rate, 4), "service_level_breaches": len(breached_high),
            "breach_rate": round(breach_rate, 4), "breach_score": breach_score,
            "conversion_score": conversion_score, "queue_rows": len(queues),
        },
    ))

    evidence_scores = [safe_int(job.get("evidence_completeness_score"), 0) for job in priority_open]
    average_evidence = round(sum(evidence_scores) / len(evidence_scores), 1) if evidence_scores else 100.0
    if average_evidence >= thresholds.evidence_watch_score:
        evidence_score = 100
    elif average_evidence >= thresholds.evidence_degraded_score:
        evidence_score = 60
    else:
        evidence_score = round(40 * average_evidence / max(1, thresholds.evidence_degraded_score))
    components.append(_component(
        "evidence_completeness", "Evidence completeness", evidence_score,
        {"average_priority_evidence_score": average_evidence, "accepted_evidence_rows": sum(1 for row in evidence if truthy(row.get("accepted")))},
    ))

    lifecycle = [
        job for job in jobs
        if is_open(job) and (
            is_verified(job)
            or authoritative(job, queues.get(str(job.get("job_key") or "")), thresholds)
        )
    ]
    lifecycle_ages = [
        age_hours(
            row_timestamp(job, "lifecycle_last_checked_at", "lifecycle_evidence_at", "last_authoritative_observation"),
            as_of,
        )
        for job in lifecycle
    ]
    unchecked = sum(1 for value in lifecycle_ages if value is None)
    stale = sum(1 for value in lifecycle_ages if value is None or value > thresholds.lifecycle_stale_hours)
    stale_rate = stale / max(1, len(lifecycle))
    components.append(_component(
        "lifecycle_health", "Lifecycle health",
        _inverse_score(stale_rate, thresholds.lifecycle_watch_stale_rate, thresholds.lifecycle_degraded_stale_rate),
        {
            "eligible_jobs": len(lifecycle),
            "unchecked_jobs": unchecked,
            "stale_jobs": stale,
            "stale_rate": round(stale_rate, 4),
        },
    ))

    ready = [
        job for job in priority_open
        if is_verified(job) and safe_int(job.get("verified_total_score") or job.get("total_score"), 0) >= thresholds.strong_fit_score
    ]
    ready_rate = len(ready) / max(1, len(priority_open))
    components.append(_component(
        "decision_readiness_health", "Decision-readiness health",
        _positive_score(ready_rate, thresholds.decision_watch_ready_rate, thresholds.decision_degraded_ready_rate),
        {"priority_open": len(priority_open), "decision_ready": len(ready), "ready_rate": round(ready_rate, 4)},
    ))
    return components, overrides
