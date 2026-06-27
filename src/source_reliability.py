from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

from src.connectors.models import ConnectorResult, SUCCESS_STATUSES, normalize_status

SOURCE_CONTROL_STATES = {"healthy", "watch", "temporarily_paused", "manual_review_required", "disabled"}
NORMALIZED_ERROR_CATEGORIES = {
    "success",
    "no_matching_jobs",
    "posting_not_found",
    "unauthorized",
    "blocked",
    "rate_limited",
    "temporary_server_failure",
    "parser_failure",
    "invalid_configuration",
    "unsupported_platform",
}

SOURCE_HEALTH_FIELDS = [
    "source_health_id",
    "company_id",
    "company_name",
    "platform",
    "source_url",
    "source_state",
    "configuration_valid",
    "last_attempted_at",
    "last_successful_at",
    "consecutive_failures",
    "attempt_count",
    "success_count",
    "failure_count",
    "success_rate_percent",
    "median_response_time_ms",
    "last_error_category",
    "last_error_message",
    "last_http_status",
    "jobs_found",
    "jobs_accepted",
    "empty_success_count",
    "rate_limit_events",
    "paused_at",
    "pause_reason",
    "manual_review_reason",
    "created_at",
    "updated_at",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "valid", "active"}


def _percent(successes: int, attempts: int) -> int:
    return round((successes / attempts) * 100) if attempts > 0 else 0


def source_health_id_for(company_id: Any, company_name: Any, platform: Any, source_url: Any = "") -> str:
    material = "|".join(
        str(value or "").strip().lower()
        for value in (company_id, company_name, platform, source_url)
        if str(value or "").strip()
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"sh_{digest}"


@dataclass(frozen=True, slots=True)
class SourceReliabilityThresholds:
    watch_consecutive_failures: int = 2
    pause_consecutive_failures: int = 3
    watch_minimum_attempts: int = 5
    watch_success_rate_percent: int = 50
    empty_success_watch_count: int = 3


@dataclass(slots=True)
class SourceHealthState:
    source_health_id: str = ""
    company_id: str = ""
    company_name: str = ""
    platform: str = ""
    source_url: str = ""
    source_state: str = "healthy"
    configuration_valid: bool = True
    last_attempted_at: str = ""
    last_successful_at: str = ""
    consecutive_failures: int = 0
    attempt_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate_percent: int = 0
    median_response_time_ms: int = 0
    last_error_category: str = ""
    last_error_message: str = ""
    last_http_status: str = ""
    jobs_found: int = 0
    jobs_accepted: int = 0
    empty_success_count: int = 0
    rate_limit_events: int = 0
    paused_at: str = ""
    pause_reason: str = ""
    manual_review_reason: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not self.source_health_id:
            self.source_health_id = source_health_id_for(self.company_id, self.company_name, self.platform, self.source_url)
        if self.source_state not in SOURCE_CONTROL_STATES:
            self.source_state = "healthy"
        self.configuration_valid = _bool(self.configuration_valid, default=True)
        for field_name in (
            "consecutive_failures",
            "attempt_count",
            "success_count",
            "failure_count",
            "success_rate_percent",
            "median_response_time_ms",
            "jobs_found",
            "jobs_accepted",
            "empty_success_count",
            "rate_limit_events",
        ):
            setattr(self, field_name, _int(getattr(self, field_name)))

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {name: values.get(name, "") for name in SOURCE_HEALTH_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "SourceHealthState":
        return cls(**{name: row.get(name, "") for name in SOURCE_HEALTH_FIELDS})


def _weighted_median_response(previous: int, attempts_before: int, observed: int) -> int:
    if observed <= 0:
        return previous
    if attempts_before <= 0 or previous <= 0:
        return observed
    return round((previous * attempts_before + observed) / (attempts_before + 1))


def apply_source_observation(
    prior: SourceHealthState,
    result: ConnectorResult,
    *,
    jobs_accepted: int = 0,
    observed_at: str | None = None,
    thresholds: SourceReliabilityThresholds | None = None,
) -> SourceHealthState:
    limits = thresholds or SourceReliabilityThresholds()
    timestamp = observed_at or utc_now_iso()
    status = normalize_status(result.status)
    success = status in SUCCESS_STATUSES
    attempts_before = prior.attempt_count
    attempt_count = attempts_before + max(1, int(result.requests or 1))
    success_count = prior.success_count + (1 if success else 0)
    failure_count = prior.failure_count + (0 if success else 1)
    consecutive_failures = 0 if success else prior.consecutive_failures + 1
    empty_success_count = prior.empty_success_count + (1 if status == "no_matching_jobs" else 0)
    rate_limit_events = prior.rate_limit_events + (1 if status == "rate_limited" or result.rate_limited else 0)
    last_error_category = "" if success else status
    last_error_message = "" if success or not result.error else result.error.message[:1000]
    last_http_status = "" if result.error is None or result.error.http_status is None else str(result.error.http_status)
    configuration_valid = status not in {"invalid_configuration", "unsupported_platform"}

    state = prior.source_state if prior.source_state == "disabled" else "healthy"
    paused_at = prior.paused_at
    pause_reason = prior.pause_reason
    manual_review_reason = prior.manual_review_reason

    if prior.source_state == "disabled":
        state = "disabled"
    elif not configuration_valid:
        state = "manual_review_required"
        manual_review_reason = last_error_message or f"Connector returned {status}"
    elif consecutive_failures >= limits.pause_consecutive_failures:
        state = "temporarily_paused"
        paused_at = prior.paused_at or timestamp
        pause_reason = last_error_message or f"{consecutive_failures} consecutive connector failures"
    elif consecutive_failures >= limits.watch_consecutive_failures:
        state = "watch"
        pause_reason = ""
    elif empty_success_count >= limits.empty_success_watch_count and result.jobs == ():
        state = "watch"
        pause_reason = ""
    elif attempt_count >= limits.watch_minimum_attempts and _percent(success_count, attempt_count) < limits.watch_success_rate_percent:
        state = "watch"
        pause_reason = ""
    elif success:
        paused_at = ""
        pause_reason = ""
        manual_review_reason = "" if prior.source_state != "manual_review_required" else manual_review_reason

    return SourceHealthState(
        source_health_id=prior.source_health_id or source_health_id_for(result.company_id, result.company_name, result.platform, result.source_url),
        company_id=result.company_id or prior.company_id,
        company_name=result.company_name or prior.company_name,
        platform=result.platform or prior.platform,
        source_url=result.source_url or prior.source_url,
        source_state=state,
        configuration_valid=configuration_valid,
        last_attempted_at=timestamp,
        last_successful_at=timestamp if success else prior.last_successful_at,
        consecutive_failures=consecutive_failures,
        attempt_count=attempt_count,
        success_count=success_count,
        failure_count=failure_count,
        success_rate_percent=_percent(success_count, attempt_count),
        median_response_time_ms=_weighted_median_response(prior.median_response_time_ms, attempts_before, result.response_time_ms),
        last_error_category=last_error_category,
        last_error_message=last_error_message,
        last_http_status=last_http_status,
        jobs_found=len(result.jobs),
        jobs_accepted=max(0, int(jobs_accepted or 0)),
        empty_success_count=empty_success_count,
        rate_limit_events=rate_limit_events,
        paused_at=paused_at,
        pause_reason=pause_reason,
        manual_review_reason=manual_review_reason,
        created_at=prior.created_at or timestamp,
        updated_at=timestamp,
    )


def read_source_health(sheet_client: Any) -> dict[str, tuple[int, SourceHealthState]]:
    try:
        if hasattr(sheet_client, "read_records_with_row_numbers"):
            rows = sheet_client.read_records_with_row_numbers("Source_Health")
        else:
            rows = [(index + 2, row) for index, row in enumerate(sheet_client.read_records("Source_Health"))]
    except Exception as exc:
        if exc.__class__.__name__ in {"WorksheetNotFound", "KeyError"}:
            return {}
        return {}
    result: dict[str, tuple[int, SourceHealthState]] = {}
    for row_number, row in rows:
        state = SourceHealthState.from_dict(dict(row))
        if state.source_health_id:
            result[state.source_health_id] = (row_number, state)
    return result


def upsert_source_health(sheet_client: Any, state: SourceHealthState, existing: dict[str, tuple[int, SourceHealthState]] | None = None) -> bool:
    index = existing if existing is not None else read_source_health(sheet_client)
    record = state.to_dict()
    current = index.get(state.source_health_id)
    try:
        if current is None:
            sheet_client.append_record("Source_Health", record)
            row_number = max((row for row, _ in index.values()), default=1) + 1
            index[state.source_health_id] = (row_number, state)
            return True
        row_number, prior = current
        prior_record = prior.to_dict()
        if prior_record == record:
            return False
        if prior_record.get("created_at"):
            record["created_at"] = prior_record["created_at"]
        sheet_client.update_record("Source_Health", row_number, record)
        index[state.source_health_id] = (row_number, SourceHealthState.from_dict(record))
        return True
    except Exception:
        return False


def observe_connector_result(
    sheet_client: Any,
    result: ConnectorResult,
    *,
    jobs_accepted: int = 0,
    observed_at: str | None = None,
    thresholds: SourceReliabilityThresholds | None = None,
) -> SourceHealthState:
    existing = read_source_health(sheet_client)
    key = source_health_id_for(result.company_id, result.company_name, result.platform, result.source_url)
    prior = existing.get(key, (0, SourceHealthState(
        source_health_id=key,
        company_id=result.company_id,
        company_name=result.company_name,
        platform=result.platform,
        source_url=result.source_url,
        created_at=observed_at or utc_now_iso(),
    )))[1]
    state = apply_source_observation(prior, result, jobs_accepted=jobs_accepted, observed_at=observed_at, thresholds=thresholds)
    upsert_source_health(sheet_client, state, existing)
    return state


def should_skip_source(state: SourceHealthState | None) -> tuple[bool, str]:
    if state is None:
        return False, ""
    if state.source_state in {"temporarily_paused", "manual_review_required", "disabled"}:
        reason = state.pause_reason or state.manual_review_reason or f"Source is {state.source_state}"
        return True, reason
    return False, ""


def platform_health_metrics(states: Iterable[SourceHealthState]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for state in states:
        platform = state.platform or "unknown"
        row = grouped.setdefault(
            platform,
            {
                "requests": 0,
                "successes": 0,
                "failures": 0,
                "jobs_returned": 0,
                "jobs_accepted": 0,
                "average_latency_ms": 0,
                "rate_limit_events": 0,
                "paused_sources": 0,
                "invalid_configurations": 0,
                "failures_by_category": {},
                "sources": 0,
            },
        )
        row["sources"] += 1
        row["requests"] += state.attempt_count
        row["successes"] += state.success_count
        row["failures"] += state.failure_count
        row["jobs_returned"] += state.jobs_found
        row["jobs_accepted"] += state.jobs_accepted
        row["rate_limit_events"] += state.rate_limit_events
        row["paused_sources"] += int(state.source_state == "temporarily_paused")
        row["invalid_configurations"] += int(not state.configuration_valid)
        if state.last_error_category:
            by_category = row["failures_by_category"]
            by_category[state.last_error_category] = by_category.get(state.last_error_category, 0) + 1
        prior_average = row["average_latency_ms"]
        row["average_latency_ms"] = round((prior_average * (row["sources"] - 1) + state.median_response_time_ms) / row["sources"])
    return grouped
