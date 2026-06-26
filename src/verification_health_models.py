from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import yaml

BLOCKER_REASONS = {
    "no_authoritative_url",
    "authoritative_match_below_threshold",
    "source_blocked",
    "source_timeout",
    "source_not_found",
    "parser_failure",
    "missing_description",
    "missing_location",
    "missing_compensation",
    "missing_work_model",
    "retry_scheduled",
    "manual_review_required",
    "no_supported_enrichment_path",
    "enrichment_not_attempted",
    "other",
}
OPEN_STATUSES = {"open", "reopened"}
TERMINAL_STATUSES = {"confirmed closed", "closed", "expired"}
REVIEWED_STATUSES = {
    "reviewing", "interested", "watch", "deferred", "dismissed", "applied",
    "interviewing", "offer", "rejected", "withdrawn", "closed",
}
APPLICATION_STATUSES = {"applied", "interviewing", "offer", "rejected", "withdrawn"}
DISMISSED_STATUSES = {"dismissed", "rejected", "withdrawn"}


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    high_potential_hours: int = 24
    target_company_hours: int = 24
    medium_high_signal_hours: int = 72
    enrichment_failure_hours: int = 48
    provisional_without_attempt_hours: int = 168
    stale_daily_run_hours: int = 36
    lifecycle_stale_hours: int = 336
    verification_watch_breach_rate: float = 0.10
    verification_degraded_breach_rate: float = 0.25
    source_watch_failure_rate: float = 0.10
    source_degraded_failure_rate: float = 0.25
    lifecycle_watch_stale_rate: float = 0.10
    lifecycle_degraded_stale_rate: float = 0.25
    evidence_watch_score: int = 65
    evidence_degraded_score: int = 40
    decision_watch_ready_rate: float = 0.15
    decision_degraded_ready_rate: float = 0.05
    authoritative_match_min_confidence: int = 70
    strong_fit_score: int = 75
    dashboard_job_limit: int = 15
    run_history_blocker_limit: int = 100

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "HealthThresholds":
        raw = data or {}
        sections = {
            **(raw.get("service_levels") or {}),
            **(raw.get("health") or {}),
            **(raw.get("matching") or {}),
            **(raw.get("presentation") or {}),
        }
        defaults = cls()
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            default = getattr(defaults, name)
            value = sections.get(name, default)
            try:
                values[name] = float(value) if isinstance(default, float) else int(float(value))
            except (TypeError, ValueError):
                values[name] = default
        return cls(**values)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HealthThresholds":
        with Path(path).open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Verification health configuration must be a mapping")
        return cls.from_mapping(loaded)


@dataclass(frozen=True, slots=True)
class Blocker:
    reason: str
    detail: str = ""


@dataclass(slots=True)
class FunnelMetric:
    stage: str
    label: str
    current_count: int
    latest_daily_count: int
    latest_seven_day_count: int
    conversion_rate: float | None
    denominator_stage: str
    median_age_hours: float | None
    oldest_unresolved_age_hours: float | None


@dataclass(slots=True)
class AgingMetric:
    category: str
    label: str
    current_count: int
    median_age_hours: float | None
    oldest_age_hours: float | None
    service_level_hours: int | None
    breach_count: int


@dataclass(slots=True)
class HealthComponent:
    component: str
    label: str
    score: int
    classification: str
    supporting_metrics: dict[str, Any] = field(default_factory=dict)
    critical: bool = False


@dataclass(slots=True)
class VerificationHealthResult:
    run_id: str
    generated_at: str
    overall_score: int
    overall_classification: str
    funnel: list[FunnelMetric]
    aging: list[AgingMetric]
    blocker_counts: dict[str, int]
    high_potential_blockers: dict[str, str]
    sla_breaches: list[dict[str, Any]]
    health_components: list[HealthComponent]
    oldest_high_potential: list[dict[str, Any]]
    oldest_target_company: list[dict[str, Any]]
    manual_intervention: list[dict[str, Any]]
    critical_overrides: list[str]
    thresholds: HealthThresholds
    records_read: dict[str, int]

    def compact_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "overall_score": self.overall_score,
            "overall_classification": self.overall_classification,
            "funnel": [asdict(item) for item in self.funnel],
            "aging": [asdict(item) for item in self.aging],
            "blocker_counts": self.blocker_counts,
            "high_potential_blockers": dict(
                list(self.high_potential_blockers.items())[: self.thresholds.run_history_blocker_limit]
            ),
            "sla_breach_count": len(self.sla_breaches),
            "health_components": [asdict(item) for item in self.health_components],
            "critical_overrides": self.critical_overrides,
            "thresholds": asdict(self.thresholds),
            "records_read": self.records_read,
        }


def truthy(value: Any) -> bool:
    return isinstance(value, bool) and value or str(value or "").strip().lower() in {
        "1", "true", "yes", "y", "accepted", "active", "x",
    }


def identity(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("&", " and ").replace("_", " ").split())


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def parse_datetime(value: Any, *, end_of_day: bool = False) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            day = datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        parsed = datetime.combine(day, time.max if end_of_day else time.min, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def age_hours(value: Any, as_of: datetime) -> float | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (as_of - parsed).total_seconds() / 3600)


def median_value(values: Iterable[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return round(float(median(valid)), 1) if valid else None


def max_value(values: Iterable[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return round(max(valid), 1) if valid else None


def row_timestamp(row: dict[str, Any], *fields: str) -> str:
    for field_name in fields:
        value = str(row.get(field_name) or "").strip()
        if value:
            return value
    return ""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
