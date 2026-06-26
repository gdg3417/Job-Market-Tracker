from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from src.verification_health_models import AgingMetric, Blocker, HealthThresholds, age_hours, identity, max_value, median_value, row_timestamp, safe_int
from src.verification_health_state import authoritative, is_deferred, is_high, is_medium_signal, is_open, is_partial, is_target, is_verified, job_timestamp


def job_summary(job: dict[str, Any], blocker: Blocker, age: float | None) -> dict[str, Any]:
    return {
        "job_key": str(job.get("job_key") or ""),
        "company": str(job.get("company") or ""),
        "title": str(job.get("title") or ""),
        "location": str(job.get("location") or ""),
        "age_hours": round(age or 0, 1),
        "blocker": blocker.reason,
        "detail": blocker.detail,
        "url": str(job.get("canonical_url") or ""),
    }


def calculate_aging(
    jobs: list[dict[str, Any]],
    queues: dict[str, dict[str, Any]],
    blockers: dict[str, Blocker],
    company_keys: set[str],
    as_of: datetime,
    thresholds: HealthThresholds,
) -> tuple[list[AgingMetric], list[dict[str, Any]]]:
    categories: list[tuple[str, str, int | None, Callable[[dict[str, Any], Blocker], bool]]] = [
        ("high_potential_provisional", "High-potential provisional jobs", thresholds.high_potential_hours, lambda j, b: is_open(j) and is_high(j) and identity(j.get("score_status")) == "provisional"),
        ("high_potential_partially_verified", "High-potential partially verified jobs", thresholds.high_potential_hours, lambda j, b: is_open(j) and is_high(j) and is_partial(j)),
        ("target_company_provisional", "Target-company provisional jobs", thresholds.target_company_hours, lambda j, b: is_open(j) and is_target(j, company_keys) and identity(j.get("score_status")) == "provisional"),
        ("medium_potential_high_signal", "Medium-potential high-signal jobs", thresholds.medium_high_signal_hours, lambda j, b: is_open(j) and is_medium_signal(j) and not is_verified(j)),
        ("enrichment_failure", "Jobs with an enrichment failure", thresholds.enrichment_failure_hours, lambda j, b: b.reason in {"source_blocked", "source_timeout", "source_not_found", "parser_failure", "no_supported_enrichment_path"}),
        ("no_authoritative_url", "Jobs with no authoritative URL", thresholds.provisional_without_attempt_hours, lambda j, b: is_open(j) and not is_verified(j) and not authoritative(j, queues.get(str(j.get("job_key") or "")), thresholds)),
        ("no_successful_enrichment_attempt", "Jobs with no successful enrichment attempt", thresholds.provisional_without_attempt_hours, lambda j, b: is_open(j) and not is_verified(j) and safe_int((queues.get(str(j.get("job_key") or "")) or {}).get("attempt_count"), 0) <= 0 and not row_timestamp(j, "enrichment_last_attempted_at")),
        ("awaiting_retry", "Jobs awaiting retry", thresholds.enrichment_failure_hours, lambda j, b: b.reason == "retry_scheduled"),
        ("manually_deferred", "Jobs manually deferred", None, lambda j, b: is_deferred(j, as_of)),
    ]
    metrics: list[AgingMetric] = []
    breaches: dict[str, dict[str, Any]] = {}
    for category, label, service_level, predicate in categories:
        selected = []
        for job in jobs:
            key = str(job.get("job_key") or "")
            blocker = blockers.get(key, Blocker("other"))
            if predicate(job, blocker):
                selected.append((job, age_hours(job_timestamp(job), as_of), blocker))
        breach_count = 0
        for job, age, blocker in selected:
            if service_level is not None and age is not None and age > service_level:
                breach_count += 1
                key = str(job.get("job_key") or "")
                candidate = job_summary(job, blocker, age) | {"service_level_hours": service_level, "category": category}
                if key not in breaches or service_level < breaches[key]["service_level_hours"]:
                    breaches[key] = candidate
        metrics.append(AgingMetric(
            category, label, len(selected),
            median_value(age for _, age, _ in selected),
            max_value(age for _, age, _ in selected),
            service_level, breach_count,
        ))
    return metrics, sorted(breaches.values(), key=lambda row: row["age_hours"], reverse=True)
