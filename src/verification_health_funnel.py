from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.verification_health_models import (
    FunnelMetric,
    HealthThresholds,
    age_hours,
    max_value,
    median_value,
    parse_datetime,
    row_timestamp,
    safe_int,
    truthy,
)
from src.verification_health_state import (
    authoritative,
    daily_run,
    is_applied,
    is_closed,
    is_daily_completion_run,
    is_dismissed,
    is_excluded,
    is_high,
    is_medium_signal,
    is_open,
    is_partial,
    is_reviewed,
    is_verified,
    job_timestamp,
    parse_notes,
)

CENTRAL_TIMEZONE = ZoneInfo("America/Chicago")

FUNNEL_STAGES = [
    ("leads_received", "Leads received", ""),
    ("jobs_normalized", "Jobs normalized", "leads_received"),
    ("jobs_accepted", "Jobs accepted", "jobs_normalized"),
    ("high_potential", "High-potential jobs identified", "jobs_accepted"),
    ("enrichment_eligible", "Enrichment eligible", "high_potential"),
    ("enrichment_attempted", "Enrichment attempted", "enrichment_eligible"),
    ("authoritative_posting_found", "Authoritative posting found", "enrichment_attempted"),
    ("evidence_accepted", "Evidence accepted", "authoritative_posting_found"),
    ("partially_verified", "Partially verified", "evidence_accepted"),
    ("fully_verified", "Fully verified", "evidence_accepted"),
    ("verified_strong_fit", "Verified strong fit", "fully_verified"),
    ("human_reviewed", "Human reviewed", "verified_strong_fit"),
    ("applied", "Applied", "human_reviewed"),
    ("dismissed", "Dismissed", "human_reviewed"),
    ("closed", "Closed", "jobs_accepted"),
]


def _within(value: Any, start: datetime | None, end: datetime) -> bool:
    parsed = parse_datetime(value)
    return bool(parsed and (start is None or parsed >= start) and parsed <= end)


def _daily_window(run: dict[str, Any] | None, as_of: datetime) -> tuple[datetime | None, datetime]:
    if not run:
        return None, as_of
    if is_daily_completion_run(run):
        notes = parse_notes(run)
        day_text = str(notes.get("central_date") or "").strip()
        selected_date: date | None = None
        if day_text:
            try:
                selected_date = date.fromisoformat(day_text[:10])
            except ValueError:
                selected_date = None
        if selected_date is None:
            finished = parse_datetime(row_timestamp(run, "finished_at", "started_at", "created_at"))
            if finished is not None:
                selected_date = finished.astimezone(CENTRAL_TIMEZONE).date()
        if selected_date is not None:
            start = datetime.combine(selected_date, time.min, tzinfo=CENTRAL_TIMEZONE).astimezone(UTC)
            end = datetime.combine(selected_date, time.max, tzinfo=CENTRAL_TIMEZONE).astimezone(UTC)
            return start, min(end, as_of)
    start = parse_datetime(row_timestamp(run, "started_at"))
    end = parse_datetime(row_timestamp(run, "finished_at")) or as_of
    return start, min(end, as_of)


def _stage_timestamp(stage: str, row: dict[str, Any]) -> str:
    fields = {
        "enrichment_attempted": ("enrichment_last_attempted_at", "updated_at"),
        "authoritative_posting_found": ("enrichment_completed_at", "updated_at"),
        "evidence_accepted": ("enrichment_completed_at", "updated_at"),
        "partially_verified": ("enrichment_completed_at", "updated_at"),
        "fully_verified": ("enrichment_completed_at", "updated_at"),
        "verified_strong_fit": ("enrichment_completed_at", "updated_at"),
        "human_reviewed": ("reviewed_date", "reviewed_at", "updated_at"),
        "applied": ("application_date", "last_application_update", "updated_at"),
        "dismissed": ("reviewed_date", "updated_at"),
        "closed": ("closed_date", "closure_confirmed_date", "updated_at"),
    }
    return row_timestamp(row, *(fields.get(stage) or ("created_at", "first_seen_date", "updated_at")))


def calculate_funnel(
    jobs: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    queues: dict[str, dict[str, Any]],
    evidence: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    as_of: datetime,
    thresholds: HealthThresholds,
) -> list[FunnelMetric]:
    accepted_keys = {str(row.get("job_key") or "") for row in evidence if truthy(row.get("accepted"))}
    normalized = lambda row: bool(
        str(row.get("job_key") or "").strip()
        and str(row.get("company") or "").strip()
        and str(row.get("title") or "").strip()
    )
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {
        "jobs_normalized": normalized,
        "jobs_accepted": lambda row: normalized(row) and not is_excluded(row),
        "high_potential": lambda row: is_open(row) and is_high(row),
        "enrichment_eligible": lambda row: is_open(row) and not is_verified(row) and (is_high(row) or is_medium_signal(row)),
        "enrichment_attempted": lambda row: safe_int((queues.get(str(row.get("job_key"))) or {}).get("attempt_count"), 0) > 0 or bool(row_timestamp(row, "enrichment_last_attempted_at")),
        "authoritative_posting_found": lambda row: authoritative(row, queues.get(str(row.get("job_key"))), thresholds),
        "evidence_accepted": lambda row: str(row.get("job_key") or "") in accepted_keys,
        "partially_verified": is_partial,
        "fully_verified": is_verified,
        "verified_strong_fit": lambda row: is_verified(row) and safe_int(row.get("verified_total_score") or row.get("total_score"), 0) >= thresholds.strong_fit_score,
        "human_reviewed": is_reviewed,
        "applied": is_applied,
        "dismissed": is_dismissed,
        "closed": is_closed,
    }
    latest = daily_run(runs)
    daily_start, daily_end = _daily_window(latest, as_of)
    seven_start = as_of - timedelta(days=7)
    selected = {stage: [] for stage, _, _ in FUNNEL_STAGES}
    selected["leads_received"] = sources
    for stage in selected:
        if stage != "leads_received":
            selected[stage] = [row for row in jobs if predicates[stage](row)]
    counts = {stage: len(rows) for stage, rows in selected.items()}
    metrics: list[FunnelMetric] = []
    for stage, label, denominator in FUNNEL_STAGES:
        rows = selected[stage]
        timestamp = lambda row: job_timestamp(row) if stage == "leads_received" else _stage_timestamp(stage, row)
        current = counts[stage]
        prior = counts.get(denominator, 0)
        ages = [age_hours(timestamp(row), as_of) for row in rows]
        unresolved = ages if stage in {"high_potential", "enrichment_eligible", "partially_verified"} else []
        metrics.append(FunnelMetric(
            stage=stage,
            label=label,
            current_count=current,
            latest_daily_count=sum(1 for row in rows if daily_start and _within(timestamp(row), daily_start, daily_end)),
            latest_seven_day_count=sum(1 for row in rows if _within(timestamp(row), seven_start, as_of)),
            conversion_rate=round(current / prior, 4) if denominator and prior else None,
            denominator_stage=denominator,
            median_age_hours=median_value(ages),
            oldest_unresolved_age_hours=max_value(unresolved),
        ))
    return metrics
