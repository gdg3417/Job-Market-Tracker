from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from src.verification_health_aging import calculate_aging, job_summary
from src.verification_health_blockers import classify_blocker
from src.verification_health_funnel import calculate_funnel
from src.verification_health_models import BLOCKER_REASONS, Blocker, HealthThresholds, VerificationHealthResult, age_hours, utc_now
from src.verification_health_scoring import SEVERITY, calculate_components, classification
from src.verification_health_state import (
    daily_run,
    is_excluded,
    is_high,
    is_open,
    is_target,
    is_verified,
    job_timestamp,
    latest_by_job,
    target_keys,
)


def calculate_verification_health(
    *,
    jobs: list[dict[str, Any]],
    job_sources: list[dict[str, Any]],
    queue_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    runs_rows: list[dict[str, Any]],
    resolution_rows: list[dict[str, Any]] | None = None,
    target_company_rows: list[dict[str, Any]] | None = None,
    config_company_rows: list[dict[str, Any]] | None = None,
    thresholds: HealthThresholds | None = None,
    as_of: datetime | None = None,
    run_id: str = "",
) -> VerificationHealthResult:
    limits = thresholds or HealthThresholds()
    now = as_of or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)

    queues = latest_by_job(queue_rows, "updated_at", "last_attempted_at", "created_at")
    resolutions = latest_by_job(resolution_rows or [], "updated_at", "attempted_at", "created_at")
    evidence_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_job.setdefault(str(row.get("job_key") or ""), []).append(row)

    blockers: dict[str, Blocker] = {}
    for job in jobs:
        if not is_open(job) or is_verified(job) or is_excluded(job):
            continue
        key = str(job.get("job_key") or "")
        blocker = classify_blocker(
            job,
            queues.get(key),
            evidence_by_job.get(key, []),
            resolutions.get(key),
            as_of=now,
            thresholds=limits,
        )
        blockers[key] = blocker if blocker.reason in BLOCKER_REASONS else Blocker("other", blocker.detail)

    company_keys = target_keys(target_company_rows or [], config_company_rows or [])
    funnel = calculate_funnel(jobs, job_sources, queues, resolutions, evidence_rows, runs_rows, now, limits)
    aging, breaches = calculate_aging(jobs, queues, resolutions, blockers, company_keys, now, limits)
    components, overrides = calculate_components(jobs, runs_rows, queues, resolutions, evidence_rows, breaches, now, limits)

    average = round(sum(item.score for item in components) / max(1, len(components)))
    score = min(average, min(item.score for item in components) + 20)
    overall = classification(score)
    worst = max((item.classification for item in components), key=lambda name: SEVERITY[name])
    if SEVERITY[worst] > SEVERITY[overall]:
        overall = worst
    if overrides:
        overall = "Blocked"
        score = min(score, 20)

    blocker_counts = Counter(blocker.reason for blocker in blockers.values())
    jobs_by_key = {str(job.get("job_key") or ""): job for job in jobs if str(job.get("job_key") or "")}
    summaries = [
        job_summary(job, blockers[str(job.get("job_key") or "")], age_hours(job_timestamp(job), now))
        for job in jobs if str(job.get("job_key") or "") in blockers
    ]
    summaries.sort(key=lambda row: row["age_hours"], reverse=True)
    oldest_high = [row for row in summaries if is_high(jobs_by_key[row["job_key"]])][: limits.dashboard_job_limit]
    oldest_target = [row for row in summaries if is_target(jobs_by_key[row["job_key"]], company_keys)][: limits.dashboard_job_limit]
    manual_reasons = {
        "manual_review_required", "source_blocked", "parser_failure",
        "no_supported_enrichment_path", "authoritative_match_below_threshold",
    }
    manual = [row for row in summaries if row["blocker"] in manual_reasons][: limits.dashboard_job_limit]

    latest = daily_run(runs_rows)
    anchor = str((latest or {}).get("run_id") or "").strip() or now.strftime("%Y%m%d")
    normalized_anchor = "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in anchor)
    effective_run_id = run_id or f"sprint33_verification_health_{normalized_anchor}"

    return VerificationHealthResult(
        run_id=effective_run_id,
        generated_at=now.isoformat().replace("+00:00", "Z"),
        overall_score=score,
        overall_classification=overall,
        funnel=funnel,
        aging=aging,
        blocker_counts=dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))),
        high_potential_blockers={key: blocker.reason for key, blocker in blockers.items() if is_high(jobs_by_key[key])},
        sla_breaches=breaches,
        health_components=components,
        oldest_high_potential=oldest_high,
        oldest_target_company=oldest_target,
        manual_intervention=manual,
        critical_overrides=overrides,
        thresholds=limits,
        records_read={
            "jobs": len(jobs), "job_sources": len(job_sources), "queue": len(queue_rows),
            "evidence": len(evidence_rows), "resolutions": len(resolution_rows or []), "runs": len(runs_rows),
        },
    )
