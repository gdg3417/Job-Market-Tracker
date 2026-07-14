from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from src.verification_health_actionability import (
    classify_actionability,
    has_valid_job_identity,
    partition_actionable_jobs,
)
from src.verification_health_aging import calculate_aging, job_summary
from src.verification_health_blockers import classify_blocker, supporting_gaps
from src.verification_health_funnel import calculate_funnel
from src.verification_health_models import BLOCKER_REASONS, Blocker, HealthThresholds, VerificationHealthResult, age_hours, safe_int, truthy, utc_now
from src.verification_health_scoring import SEVERITY, calculate_components, classification
from src.verification_health_state import (
    authoritative,
    daily_run,
    is_closed,
    is_high,
    is_target,
    is_verified,
    job_timestamp,
    latest_by_job,
    target_keys,
)

MANUAL_BLOCKER_REASONS = {
    "manual_review_required",
    "source_blocked",
    "parser_failure",
    "no_supported_enrichment_path",
    "authoritative_match_below_threshold",
}
MANUAL_ACTIONABILITY_REASONS = {
    "closure_confirmation_required",
    "deferred_missing_due_date",
    "deferred_invalid_due_date",
}


def _rows_with_job_key(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude blank worksheet rows before job-keyed calculations."""
    return [row for row in rows if str(row.get("job_key") or "").strip()]


def _job_rows_with_identity_signal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep every nonblank Jobs row visible to the identity audit."""
    return [row for row in rows if any(str(value or "").strip() for value in row.values())]


def _portfolio_coverage(
    jobs: list[dict[str, Any]],
    queues: dict[str, dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    thresholds: HealthThresholds,
) -> dict[str, Any]:
    valid = [job for job in jobs if has_valid_job_identity(job)]
    valid_keys = {str(job.get("job_key") or "").strip() for job in valid}
    accepted_evidence_keys = {
        str(row.get("job_key") or "").strip()
        for row in evidence_rows
        if truthy(row.get("accepted")) and str(row.get("job_key") or "").strip() in valid_keys
    }
    authoritative_keys = {
        str(job.get("job_key") or "").strip()
        for job in valid
        if authoritative(
            job,
            queues.get(str(job.get("job_key") or "").strip()),
            thresholds,
            resolutions.get(str(job.get("job_key") or "").strip()),
        )
    }
    verified_keys = {
        str(job.get("job_key") or "").strip()
        for job in valid
        if is_verified(job)
    }
    covered_keys = accepted_evidence_keys | authoritative_keys | verified_keys
    evidence_scores = [
        max(0, min(100, safe_int(job.get("evidence_completeness_score"), 0)))
        for job in valid
        if str(job.get("evidence_completeness_score") or "").strip()
    ]
    return {
        "portfolio_jobs": len(valid),
        "portfolio_open_or_uncertain": sum(1 for job in valid if not is_closed(job)),
        "portfolio_terminal": sum(1 for job in valid if is_closed(job)),
        "portfolio_verified": len(verified_keys),
        "portfolio_authoritative": len(authoritative_keys),
        "portfolio_with_accepted_evidence": len(accepted_evidence_keys),
        "portfolio_covered_jobs": len(covered_keys),
        "portfolio_coverage_rate": round(len(covered_keys) / len(valid), 4) if valid else 1.0,
        "average_evidence_completeness_score": round(sum(evidence_scores) / len(evidence_scores), 1) if evidence_scores else 0.0,
        "invalid_identity_rows": sum(1 for job in jobs if not has_valid_job_identity(job)),
    }


def _overall_reasons(
    *,
    overrides: list[str],
    components: list[Any],
    breaches: list[dict[str, Any]],
    manual_count: int,
) -> list[str]:
    reasons: list[str] = []
    reasons.extend(overrides)
    if breaches:
        reasons.append(f"{len(breaches)} actionable role(s) exceed a verification service level.")
    if manual_count:
        reasons.append(f"{manual_count} actionable role(s) require manual intervention.")
    for component in sorted(
        (item for item in components if item.classification != "Healthy"),
        key=lambda item: (-SEVERITY[item.classification], item.score, item.label),
    ):
        reasons.append(f"{component.label} is {component.classification.lower()} at {component.score}.")
    if not reasons:
        reasons.append("No current actionable verification concerns were detected.")
    deduplicated: list[str] = []
    for reason in reasons:
        if reason not in deduplicated:
            deduplicated.append(reason)
    return deduplicated[:5]


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

    raw_resolution_rows = resolution_rows or []
    records_read = {
        "jobs": len(jobs),
        "job_sources": len(job_sources),
        "queue": len(queue_rows),
        "evidence": len(evidence_rows),
        "resolutions": len(raw_resolution_rows),
        "runs": len(runs_rows),
    }
    jobs = _job_rows_with_identity_signal(jobs)
    job_sources = _rows_with_job_key(job_sources)
    queue_rows = _rows_with_job_key(queue_rows)
    evidence_rows = _rows_with_job_key(evidence_rows)
    resolution_rows = _rows_with_job_key(raw_resolution_rows)

    queues = latest_by_job(queue_rows, "updated_at", "last_attempted_at", "created_at")
    resolutions = latest_by_job(resolution_rows, "updated_at", "attempted_at", "created_at")
    evidence_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_job.setdefault(str(row.get("job_key") or ""), []).append(row)

    actionable_jobs, actionability, exclusion_counts = partition_actionable_jobs(jobs, as_of=now)
    blockers: dict[str, Blocker] = {}
    secondary_gap_counts: Counter[str] = Counter()
    for job in actionable_jobs:
        key = str(job.get("job_key") or "").strip()
        state = actionability.get(key) or classify_actionability(job, as_of=now)
        if state.reason in MANUAL_ACTIONABILITY_REASONS:
            blocker = Blocker("manual_review_required", state.detail)
        elif is_verified(job):
            continue
        else:
            blocker = classify_blocker(
                job,
                queues.get(key),
                evidence_by_job.get(key, []),
                resolutions.get(key),
                as_of=now,
                thresholds=limits,
            )
        blockers[key] = blocker if blocker.reason in BLOCKER_REASONS else Blocker("other", blocker.detail)
        for gap in supporting_gaps(
            job,
            queues.get(key),
            evidence_by_job.get(key, []),
            resolutions.get(key),
            thresholds=limits,
        ):
            if gap != blockers[key].reason:
                secondary_gap_counts[gap] += 1

    company_keys = target_keys(target_company_rows or [], config_company_rows or [])
    funnel = calculate_funnel(jobs, job_sources, queues, resolutions, evidence_rows, runs_rows, now, limits)
    aging, breaches = calculate_aging(actionable_jobs, queues, resolutions, blockers, company_keys, now, limits)
    components, overrides = calculate_components(actionable_jobs, runs_rows, queues, resolutions, evidence_rows, breaches, now, limits)

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
    ownership_counts = Counter(
        "manual_intervention" if blocker.reason in MANUAL_BLOCKER_REASONS else "system_work"
        for blocker in blockers.values()
    )
    jobs_by_key = {
        str(job.get("job_key") or ""): job
        for job in actionable_jobs
        if str(job.get("job_key") or "")
    }
    summaries = [
        job_summary(job, blockers[str(job.get("job_key") or "")], age_hours(job_timestamp(job), now))
        for job in actionable_jobs if str(job.get("job_key") or "") in blockers
    ]
    summaries.sort(key=lambda row: row["age_hours"], reverse=True)
    oldest_high = [
        row for row in summaries
        if row["job_key"] in jobs_by_key and is_high(jobs_by_key[row["job_key"]])
    ][: limits.dashboard_job_limit]
    oldest_target = [
        row for row in summaries
        if row["job_key"] in jobs_by_key and is_target(jobs_by_key[row["job_key"]], company_keys)
    ][: limits.dashboard_job_limit]
    manual_all = [
        row for row in summaries
        if row["blocker"] in MANUAL_BLOCKER_REASONS
    ]
    manual = manual_all[: limits.dashboard_job_limit]

    actionable_summary = {
        "actionable_roles": len(actionable_jobs),
        "actionable_open_roles": sum(1 for job in actionable_jobs if not is_closed(job)),
        "actionable_high_potential": sum(1 for job in actionable_jobs if is_high(job)),
        "actionable_unverified": sum(1 for job in actionable_jobs if not is_verified(job)),
        "actionable_primary_blockers": len(blockers),
        "aged_actionable_roles": len({str(row.get("job_key") or "") for row in breaches}),
        "manual_interventions_required": len(manual_all),
        "active_applications": sum(
            1 for key, result in actionability.items()
            if result.actionable and result.reason == "active_application" and not key.startswith("__invalid_row_")
        ),
        "closure_confirmations_required": sum(
            1 for key, result in actionability.items()
            if result.actionable and result.reason == "closure_confirmation_required" and not key.startswith("__invalid_row_")
        ),
        "dismissed_roles_excluded": exclusion_counts.get("dismissed", 0),
        "deferred_not_due_excluded": exclusion_counts.get("deferred_not_due", 0),
    }
    portfolio = _portfolio_coverage(jobs, queues, resolutions, evidence_rows, limits)
    reasons = _overall_reasons(
        overrides=overrides,
        components=components,
        breaches=breaches,
        manual_count=len(manual_all),
    )

    latest = daily_run(runs_rows)
    anchor = str((latest or {}).get("run_id") or "").strip() or now.strftime("%Y%m%d")
    normalized_anchor = "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in anchor)
    effective_run_id = run_id or f"sprint33_verification_health_{normalized_anchor}"

    return VerificationHealthResult(
        run_id=effective_run_id,
        generated_at=now.isoformat().replace("+00:00", "Z"),
        overall_score=score,
        overall_classification=overall,
        overall_reasons=reasons,
        funnel=funnel,
        aging=aging,
        blocker_counts=dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))),
        secondary_gap_counts=dict(sorted(secondary_gap_counts.items(), key=lambda item: (-item[1], item[0]))),
        blocker_ownership_counts=dict(sorted(ownership_counts.items())),
        high_potential_blockers={
            key: blocker.reason
            for key, blocker in blockers.items()
            if key in jobs_by_key and is_high(jobs_by_key[key])
        },
        sla_breaches=breaches,
        health_components=components,
        oldest_high_potential=oldest_high,
        oldest_target_company=oldest_target,
        manual_intervention=manual,
        actionable_summary=actionable_summary,
        portfolio_coverage=portfolio,
        actionability_exclusions=exclusion_counts,
        critical_overrides=overrides,
        thresholds=limits,
        records_read=records_read,
    )
