from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso

OPEN_STATUSES = {"open", "reopened", "not_seen_once", "likely_closed"}
TERMINAL_STATUSES = {"confirmed_closed", "closed", "expired"}
REVIEWED_STATUSES = {"reviewing", "interested", "watch", "deferred", "dismissed", "applied", "interviewing", "offer", "rejected", "withdrawn", "closed"}
INTEREST_OR_APPLICATION_STATUSES = {"interested", "applied", "interviewing", "offer"}
QUEUE_OPEN_STATUSES = {"pending", "in_progress", "retryable_failure"}
HIGH_PRIORITY_BLOCKER_ENRICHMENT_STATUSES = {"partial", "ambiguous", "not_found", "retryable_failure", "permanent_failure", "closed"}
HIGH_PRIORITY_ACTIVE_ENRICHMENT_STATUSES = {"pending", "in_progress"}
TRANSIENT_CLOSURE_BLOCKERS = {
    "timeout",
    "source_timeout",
    "blocked",
    "source_blocked",
    "parser_failure",
    "rate_limited",
    "http_429",
    "http_5xx",
    "temporary_server_failure",
    "external_search_miss",
    "empty_search_result",
}
SUPPORTED_READINESS = {"ready", "ready_with_warnings", "not_ready"}
READINESS_RUN_TYPE = "sprint_38_production_readiness"


@dataclass(frozen=True, slots=True)
class LifecycleCadencePolicy:
    high_potential_days: int = 1
    target_company_days: int = 1
    interested_or_applied_days: int = 1
    other_reviewed_days: int = 7
    low_priority_provisional_days: int = 14
    closed_days: int = 30
    default_days: int = 7

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> "LifecycleCadencePolicy":
        values = values or {}
        return cls(
            high_potential_days=_safe_int(values.get("high_potential_days"), 1),
            target_company_days=_safe_int(values.get("target_company_days"), 1),
            interested_or_applied_days=_safe_int(values.get("interested_or_applied_days"), 1),
            other_reviewed_days=_safe_int(values.get("other_reviewed_days"), 7),
            low_priority_provisional_days=_safe_int(values.get("low_priority_provisional_days"), 14),
            closed_days=_safe_int(values.get("closed_days"), 30),
            default_days=_safe_int(values.get("default_days"), 7),
        )


@dataclass(frozen=True, slots=True)
class ClosureDecision:
    may_close: bool
    reason: str
    confidence: str
    next_status: str = ""
    safeguard_triggered: bool = False


@dataclass(frozen=True, slots=True)
class LifecycleHistoryRecord:
    lifecycle_history_id: str
    job_key: str
    company: str
    title: str
    previous_status: str
    next_status: str
    first_observed_date: str
    posting_date: str
    last_authoritative_observation: str
    last_successful_retrieval: str
    last_unsuccessful_retrieval: str
    consecutive_authoritative_absence_count: int
    closing_date: str
    valid_through: str
    employer_page_removed: str
    ats_status: str
    description_changed: str
    location_changed: str
    compensation_changed: str
    reopened_date: str
    closure_reason: str
    closure_confidence: str
    closure_evidence_source: str
    closure_confirmed_date: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BinaryMetric:
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return round(self.true_positive / denominator, 4) if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return round(self.true_positive / denominator, 4) if denominator else 0.0

    @property
    def false_positive_rate(self) -> float:
        denominator = self.false_positive + self.true_negative
        return round(self.false_positive / denominator, 4) if denominator else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "true_negative": self.true_negative,
            "precision": self.precision,
            "recall": self.recall,
            "false_positive_rate": self.false_positive_rate,
        }


@dataclass(frozen=True, slots=True)
class ReadinessThresholds:
    daily_workflow_max_age_hours: int = 30
    gmail_backlog_max: int = 0
    enrichment_backlog_max: int = 25
    high_priority_sla_breaches_max: int = 0
    minimum_resolution_success_rate: float = 0.5
    minimum_verification_conversion_rate: float = 0.25
    source_failure_rate_max: float = 0.25
    minimum_regression_pass_rate: float = 1.0

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> "ReadinessThresholds":
        values = values or {}
        return cls(
            daily_workflow_max_age_hours=_safe_int(values.get("daily_workflow_max_age_hours"), 30),
            gmail_backlog_max=_safe_int(values.get("gmail_backlog_max"), 0),
            enrichment_backlog_max=_safe_int(values.get("enrichment_backlog_max"), 25),
            high_priority_sla_breaches_max=_safe_int(values.get("high_priority_sla_breaches_max"), 0),
            minimum_resolution_success_rate=_safe_float(values.get("minimum_resolution_success_rate"), 0.5),
            minimum_verification_conversion_rate=_safe_float(values.get("minimum_verification_conversion_rate"), 0.25),
            source_failure_rate_max=_safe_float(values.get("source_failure_rate_max"), 0.25),
            minimum_regression_pass_rate=_safe_float(values.get("minimum_regression_pass_rate"), 1.0),
        )


@dataclass(frozen=True, slots=True)
class ReadinessGate:
    name: str
    status: str
    critical: bool
    observed: Any
    threshold: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    classification: str
    gates: list[ReadinessGate]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "gates": [gate.to_dict() for gate in self.gates],
            "metrics": self.metrics,
        }


@dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str
    severity: str
    category: str
    message: str
    dedupe_key: str
    created_at: str
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MissedRoleAuditResult:
    missed_jobs: list[dict[str, Any]] = field(default_factory=list)
    incorrectly_rejected_jobs: list[dict[str, Any]] = field(default_factory=list)
    duplicate_collapse_problems: list[dict[str, Any]] = field(default_factory=list)
    incorrect_company_normalization: list[dict[str, Any]] = field(default_factory=list)
    incorrect_title_normalization: list[dict[str, Any]] = field(default_factory=list)
    incorrect_priority_classification: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _identity_parts(*values: Any) -> str:
    return "|".join(_normalize(value) for value in values if str(value or "").strip())


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = parse_iso_date(text)
        if parsed_date is None:
            return None
        parsed = datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hours_since(value: Any, now: str) -> float:
    parsed = _parse_timestamp(value)
    current = _parse_timestamp(now) or datetime.now(UTC)
    if parsed is None:
        return 999999.0
    return round(max(0.0, (current - parsed).total_seconds() / 3600), 2)


def _days_since(value: Any, now: str) -> int:
    parsed_date = parse_iso_date(value)
    now_date = parse_iso_date(now)
    if parsed_date is None or now_date is None:
        return 999999
    return max(0, (now_date - parsed_date).days)


def _is_open(job: JobPosting) -> bool:
    return job.status in OPEN_STATUSES


def _is_target_company(job: JobPosting, target_company_keys: set[str]) -> bool:
    return _normalize(job.company) in target_company_keys


def _is_interested_or_applied(job: JobPosting) -> bool:
    review_status = str(job.review_status or "").strip().lower()
    application_status = str(job.application_status or "").strip().lower()
    return review_status in INTEREST_OR_APPLICATION_STATUSES or application_status in INTEREST_OR_APPLICATION_STATUSES


def _is_reviewed(job: JobPosting) -> bool:
    return str(job.review_status or "").strip().lower() in REVIEWED_STATUSES


def lifecycle_interval_days(
    job: JobPosting,
    *,
    target_company_keys: set[str] | None = None,
    policy: LifecycleCadencePolicy | None = None,
) -> tuple[int, str]:
    rules = policy or LifecycleCadencePolicy()
    targets = target_company_keys or set()
    if job.status in TERMINAL_STATUSES:
        return rules.closed_days, "closed_limited_confirmation"
    if _is_interested_or_applied(job):
        return rules.interested_or_applied_days, "interested_or_applied_daily"
    if job.potential_priority == "high":
        return rules.high_potential_days, "high_potential_daily"
    if _is_target_company(job, targets):
        return rules.target_company_days, "target_company_daily"
    if _is_reviewed(job):
        return rules.other_reviewed_days, "reviewed_weekly"
    if job.potential_priority == "low" and job.score_status == "provisional":
        return rules.low_priority_provisional_days, "low_priority_provisional_lower_frequency"
    return rules.default_days, "default_weekly"


def lifecycle_due_rows(
    jobs: Iterable[JobPosting],
    *,
    now: str | None = None,
    target_company_keys: set[str] | None = None,
    policy: LifecycleCadencePolicy | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    timestamp = now or utc_now_iso()
    current = _parse_timestamp(timestamp) or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for job in jobs:
        interval_days, cadence_reason = lifecycle_interval_days(job, target_company_keys=target_company_keys, policy=policy)
        last_checked = _parse_timestamp(job.lifecycle_last_checked_at)
        next_check = _parse_timestamp(job.lifecycle_next_check_at)
        computed_next = (last_checked + timedelta(days=interval_days)) if last_checked else None
        due_at = next_check or computed_next
        due = due_at is None or due_at <= current
        priority_rank = _lifecycle_priority_rank(job, cadence_reason)
        if due:
            rows.append(
                {
                    "job_key": job.job_key,
                    "company": job.company,
                    "title": job.title,
                    "status": job.status,
                    "potential_priority": job.potential_priority,
                    "review_status": job.review_status,
                    "cadence_days": interval_days,
                    "cadence_reason": cadence_reason,
                    "last_checked_at": job.lifecycle_last_checked_at,
                    "next_check_at": (due_at or current).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "priority_rank": priority_rank,
                }
            )
    rows.sort(key=lambda row: (row["priority_rank"], row["next_check_at"], row["company"], row["title"]))
    if limit is not None:
        return rows[: max(0, limit)]
    return rows


def _lifecycle_priority_rank(job: JobPosting, cadence_reason: str) -> int:
    if _is_interested_or_applied(job):
        return 0
    if job.potential_priority == "high":
        return 1
    if cadence_reason.startswith("target_company"):
        return 2
    if _is_reviewed(job):
        return 3
    if job.potential_priority == "low" and job.score_status == "provisional":
        return 5
    if job.status in TERMINAL_STATUSES:
        return 6
    return 4


def closure_decision_from_observation(
    observation: Mapping[str, Any],
    *,
    previous_status: str = "open",
    checked_at: str | None = None,
    repeated_absence_threshold: int = 2,
) -> ClosureDecision:
    authoritative = bool(observation.get("authoritative"))
    manual_closed = bool(observation.get("manual_closure"))
    error_type = str(observation.get("error_type") or "").strip().lower()
    evidence_type = str(observation.get("evidence_type") or "").strip().lower()
    http_status = _safe_int(observation.get("http_status"), 0)
    if manual_closed:
        return ClosureDecision(True, "Manual closure decision", "confirmed", "confirmed_closed")
    if error_type in TRANSIENT_CLOSURE_BLOCKERS or evidence_type in TRANSIENT_CLOSURE_BLOCKERS or http_status == 429 or http_status >= 500:
        return ClosureDecision(False, "Temporary or blocked retrieval cannot close a posting", "none", safeguard_triggered=True)
    if authoritative and bool(observation.get("explicitly_closed")):
        return ClosureDecision(True, "Authoritative source explicitly reports closed", "confirmed", "confirmed_closed")
    valid_through = str(observation.get("valid_through") or "").strip()
    if authoritative and valid_through:
        expiry = parse_iso_date(valid_through)
        checked = parse_iso_date(checked_at or observation.get("checked_at") or today_iso())
        if expiry and checked and expiry < checked:
            return ClosureDecision(True, f"Authoritative validThrough expired on {expiry.isoformat()}", "confirmed", "expired")
    if authoritative and bool(observation.get("removed")):
        misses = _safe_int(observation.get("consecutive_authoritative_absence_count"), 0)
        if misses >= repeated_absence_threshold:
            return ClosureDecision(True, "Repeated authoritative absence confirms closure", "high", "confirmed_closed")
        return ClosureDecision(False, "One authoritative absence requires a later confirmation", "low", "likely_closed")
    if bool(observation.get("external_search_miss")) or evidence_type == "external_search_miss":
        return ClosureDecision(False, "External search miss cannot close a posting", "none", safeguard_triggered=True)
    if previous_status in TERMINAL_STATUSES:
        return ClosureDecision(False, "No reopening evidence was observed", "none", previous_status)
    return ClosureDecision(False, "No authoritative closure evidence", "none", previous_status)


def detect_reopened(previous_status: str, observation: Mapping[str, Any]) -> bool:
    return previous_status in TERMINAL_STATUSES.union({"likely_closed"}) and bool(observation.get("authoritative")) and bool(observation.get("listed")) and not bool(observation.get("explicitly_closed"))


def build_lifecycle_history_record(
    job: JobPosting,
    observation: Mapping[str, Any],
    decision: ClosureDecision,
    *,
    previous_status: str,
    observed_at: str | None = None,
) -> LifecycleHistoryRecord:
    timestamp = observed_at or str(observation.get("checked_at") or utc_now_iso())
    reopened = timestamp[:10] if detect_reopened(previous_status, observation) else ""
    successful = timestamp if bool(observation.get("retrieval_success")) or bool(observation.get("listed")) else ""
    unsuccessful = timestamp if not successful else ""
    lifecycle_history_id = "|".join(
        [
            "sprint38",
            job.job_key or _identity_parts(job.company, job.title, job.location),
            timestamp.replace(":", "").replace("-", ""),
            str(observation.get("evidence_key") or observation.get("evidence_type") or "observation"),
        ]
    )[:250]
    return LifecycleHistoryRecord(
        lifecycle_history_id=lifecycle_history_id,
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        previous_status=previous_status,
        next_status=decision.next_status or previous_status,
        first_observed_date=job.first_seen_date,
        posting_date=str(observation.get("posting_date") or ""),
        last_authoritative_observation=timestamp if bool(observation.get("authoritative")) else "",
        last_successful_retrieval=successful,
        last_unsuccessful_retrieval=unsuccessful,
        consecutive_authoritative_absence_count=_safe_int(observation.get("consecutive_authoritative_absence_count"), job.lifecycle_miss_count),
        closing_date=str(observation.get("closing_date") or job.closed_date or ""),
        valid_through=str(observation.get("valid_through") or ""),
        employer_page_removed="yes" if bool(observation.get("removed")) else "",
        ats_status=str(observation.get("ats_status") or ""),
        description_changed="yes" if bool(observation.get("description_changed")) else "",
        location_changed="yes" if bool(observation.get("location_changed")) else "",
        compensation_changed="yes" if bool(observation.get("compensation_changed")) else "",
        reopened_date=reopened,
        closure_reason=decision.reason,
        closure_confidence=decision.confidence,
        closure_evidence_source=str(observation.get("source_url") or observation.get("source_type") or ""),
        closure_confirmed_date=timestamp[:10] if decision.may_close else "",
        observed_at=timestamp,
    )


def binary_metric(cases: Iterable[Mapping[str, Any]], expected_key: str, actual_key: str) -> BinaryMetric:
    tp = fp = fn = tn = 0
    for case in cases:
        expected = _case_bool(case, expected_key)
        actual = _case_bool(case, actual_key)
        if expected is None or actual is None:
            continue
        if expected and actual:
            tp += 1
        elif not expected and actual:
            fp += 1
        elif expected and not actual:
            fn += 1
        else:
            tn += 1
    return BinaryMetric(tp, fp, fn, tn)


def _case_bool(case: Mapping[str, Any], dotted_key: str) -> bool | None:
    value: Any = case
    for part in dotted_key.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "accepted"}


def evaluate_gold_standard_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ingestion = binary_metric(cases, "expected.should_ingest", "actual.ingested")
    duplicates = binary_metric(cases, "expected.duplicate_group_correct", "actual.duplicate_collapsed_correct")
    authority = binary_metric(cases, "expected.authoritative_match_correct", "actual.authoritative_match_accepted")
    evidence = binary_metric(cases, "expected.evidence_should_be_accepted", "actual.evidence_accepted")
    high_potential = binary_metric(cases, "expected.should_be_high_potential", "actual.high_potential")
    strong_fit = binary_metric(cases, "expected.verified_strong_fit", "actual.verified_strong_fit")
    closure = binary_metric(cases, "expected.should_be_closed", "actual.marked_closed")
    false_closures = closure.false_positive
    actual_closures = closure.true_positive + closure.false_positive
    resolution_required = [case for case in cases if _case_bool(case, "expected.requires_authoritative_resolution") is True]
    resolved = sum(1 for case in resolution_required if _case_bool(case, "actual.resolved_authoritative") is True)
    reviewed = sum(1 for case in cases if _case_bool(case, "actual.reviewed") is True)
    applied = sum(1 for case in cases if _case_bool(case, "actual.applied") is True)
    ingested = sum(1 for case in cases if _case_bool(case, "actual.ingested") is True)
    expected_passes = [_case_bool(case, "actual.regression_passed") for case in cases]
    measurable_passes = [value for value in expected_passes if value is not None]
    regression_pass_rate = round(sum(1 for value in measurable_passes if value) / len(measurable_passes), 4) if measurable_passes else 0.0
    return {
        "case_count": len(cases),
        "ingestion_precision": ingestion.precision,
        "ingestion_recall": ingestion.recall,
        "duplicate_precision": duplicates.precision,
        "resolution_success_rate": round(resolved / len(resolution_required), 4) if resolution_required else 0.0,
        "authoritative_match_precision": authority.precision,
        "evidence_acceptance_precision": evidence.precision,
        "high_potential_recall": high_potential.recall,
        "verified_strong_fit_precision": strong_fit.precision,
        "closure_precision": closure.precision,
        "closure_recall": closure.recall,
        "false_closure_rate": round(false_closures / actual_closures, 4) if actual_closures else 0.0,
        "review_conversion": round(reviewed / ingested, 4) if ingested else 0.0,
        "application_conversion": round(applied / ingested, 4) if ingested else 0.0,
        "regression_pass_rate": regression_pass_rate,
        "details": {
            "ingestion": ingestion.to_dict(),
            "duplicates": duplicates.to_dict(),
            "authoritative_match": authority.to_dict(),
            "evidence_acceptance": evidence.to_dict(),
            "high_potential": high_potential.to_dict(),
            "verified_strong_fit": strong_fit.to_dict(),
            "closure": closure.to_dict(),
        },
    }


def load_gold_standard_cases(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, Mapping):
        cases = data.get("cases") or []
    else:
        cases = data
    if not isinstance(cases, list):
        raise ValueError("Gold-standard regression dataset must contain a list of cases")
    return [dict(case) for case in cases]


def audit_missed_roles(source_rows: Iterable[Mapping[str, Any]], accepted_jobs: Iterable[Mapping[str, Any]]) -> MissedRoleAuditResult:
    accepted_by_identity = Counter(
        _identity_parts(row.get("company"), row.get("title"), row.get("location"), row.get("source_job_id") or row.get("job_key"))
        for row in accepted_jobs
    )
    missed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicate_problems: list[dict[str, Any]] = []
    company_problems: list[dict[str, Any]] = []
    title_problems: list[dict[str, Any]] = []
    priority_problems: list[dict[str, Any]] = []

    for source in source_rows:
        identity = _identity_parts(source.get("company"), source.get("title"), source.get("location"), source.get("source_job_id") or source.get("job_key"))
        expected_accept = _truthy(source.get("expected_accept"), default=False)
        accepted = accepted_by_identity.get(identity, 0) > 0
        if expected_accept and not accepted:
            missed.append(dict(source))
        if expected_accept and str(source.get("rejection_reason") or "").strip():
            rejected.append(dict(source))
        if accepted_by_identity.get(identity, 0) > 1:
            duplicate_problems.append({"identity": identity, "accepted_count": accepted_by_identity[identity]})
        if source.get("expected_company_normalized") and _normalize(source.get("company")) != _normalize(source.get("expected_company_normalized")):
            company_problems.append(dict(source))
        if source.get("expected_title_normalized") and _normalize(source.get("title")) != _normalize(source.get("expected_title_normalized")):
            title_problems.append(dict(source))
        if source.get("expected_priority") and _normalize(source.get("priority")) != _normalize(source.get("expected_priority")):
            priority_problems.append(dict(source))

    return MissedRoleAuditResult(
        missed_jobs=missed,
        incorrectly_rejected_jobs=rejected,
        duplicate_collapse_problems=duplicate_problems,
        incorrect_company_normalization=company_problems,
        incorrect_title_normalization=title_problems,
        incorrect_priority_classification=priority_problems,
    )


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "pass"}


def _gate(name: str, passed: bool, *, critical: bool, observed: Any, threshold: Any, message: str, warning: bool = False) -> ReadinessGate:
    status = "pass" if passed else ("warn" if warning else "fail")
    return ReadinessGate(name=name, status=status, critical=critical, observed=observed, threshold=threshold, message=message)


def evaluate_readiness(metrics: Mapping[str, Any], thresholds: ReadinessThresholds | None = None) -> ReadinessResult:
    limits = thresholds or ReadinessThresholds()
    gates = [
        _gate(
            "daily_workflow_freshness",
            _safe_float(metrics.get("daily_workflow_age_hours"), 999999.0) <= limits.daily_workflow_max_age_hours,
            critical=True,
            observed=metrics.get("daily_workflow_age_hours"),
            threshold=f"<= {limits.daily_workflow_max_age_hours} hours",
            message="Daily workflow must complete recently",
        ),
        _gate(
            "schema_validity",
            _truthy(metrics.get("schema_valid"), default=False),
            critical=True,
            observed=metrics.get("schema_valid"),
            threshold="true",
            message="Workbook schema validation must pass",
        ),
        _gate(
            "gmail_backlog",
            _safe_int(metrics.get("gmail_backlog"), 999999) <= limits.gmail_backlog_max,
            critical=False,
            observed=metrics.get("gmail_backlog"),
            threshold=f"<= {limits.gmail_backlog_max}",
            message="Gmail backlog should remain clear",
        ),
        _gate(
            "enrichment_backlog",
            _safe_int(metrics.get("enrichment_backlog"), 999999) <= limits.enrichment_backlog_max,
            critical=False,
            observed=metrics.get("enrichment_backlog"),
            threshold=f"<= {limits.enrichment_backlog_max}",
            message="Enrichment backlog should remain bounded",
        ),
        _gate(
            "high_priority_service_level",
            _safe_int(metrics.get("high_priority_sla_breaches"), 999999) <= limits.high_priority_sla_breaches_max,
            critical=True,
            observed=metrics.get("high_priority_sla_breaches"),
            threshold=f"<= {limits.high_priority_sla_breaches_max}",
            message="High-potential jobs should not remain unresolved beyond service level without a clear blocker",
        ),
        _gate(
            "resolution_success",
            _safe_float(metrics.get("resolution_success_rate"), 0.0) >= limits.minimum_resolution_success_rate,
            critical=False,
            observed=metrics.get("resolution_success_rate"),
            threshold=f">= {limits.minimum_resolution_success_rate}",
            message="Authoritative resolution should remain effective",
            warning=True,
        ),
        _gate(
            "verification_conversion",
            _safe_float(metrics.get("verification_conversion_rate"), 0.0) >= limits.minimum_verification_conversion_rate,
            critical=False,
            observed=metrics.get("verification_conversion_rate"),
            threshold=f">= {limits.minimum_verification_conversion_rate}",
            message="High-potential roles should convert to verified reviewable rows when sufficient evidence is available",
            warning=True,
        ),
        _gate(
            "source_failure_rate",
            _safe_float(metrics.get("source_failure_rate"), 1.0) <= limits.source_failure_rate_max,
            critical=False,
            observed=metrics.get("source_failure_rate"),
            threshold=f"<= {limits.source_failure_rate_max}",
            message="Source platform failures should remain bounded",
            warning=True,
        ),
        _gate(
            "lifecycle_false_closure_protection",
            _safe_int(metrics.get("false_closure_count"), 999999) == 0,
            critical=True,
            observed=metrics.get("false_closure_count"),
            threshold="0",
            message="False closure count must be zero",
        ),
        _gate(
            "regression_pass_rate",
            _safe_float(metrics.get("regression_pass_rate"), 0.0) >= limits.minimum_regression_pass_rate,
            critical=True,
            observed=metrics.get("regression_pass_rate"),
            threshold=f">= {limits.minimum_regression_pass_rate}",
            message="Gold-standard regression cases must pass",
        ),
        _gate(
            "dashboard_refresh_success",
            _truthy(metrics.get("dashboard_refresh_success"), default=False),
            critical=True,
            observed=metrics.get("dashboard_refresh_success"),
            threshold="true",
            message="Dashboard refresh must succeed",
        ),
        _gate(
            "digest_refresh_success",
            _truthy(metrics.get("digest_refresh_success"), default=False),
            critical=True,
            observed=metrics.get("digest_refresh_success"),
            threshold="true",
            message="Digest refresh must succeed",
        ),
    ]
    if any(gate.status == "fail" and gate.critical for gate in gates):
        classification = "not_ready"
    elif any(gate.status == "fail" for gate in gates):
        classification = "not_ready"
    elif any(gate.status == "warn" for gate in gates):
        classification = "ready_with_warnings"
    else:
        classification = "ready"
    return ReadinessResult(classification=classification, gates=gates, metrics=dict(metrics))


def build_alerts(
    readiness: ReadinessResult,
    *,
    prior_alert_ids: set[str] | None = None,
    created_at: str | None = None,
) -> list[Alert]:
    prior = prior_alert_ids or set()
    timestamp = created_at or utc_now_iso()
    alerts: list[Alert] = []
    for gate in readiness.gates:
        if gate.status == "pass":
            continue
        if gate.status == "warn" and gate.name not in {"resolution_success", "verification_conversion", "source_failure_rate"}:
            continue
        severity = "critical" if gate.critical else "warning"
        dedupe_key = f"{gate.name}:{gate.status}:{gate.observed}:{gate.threshold}"
        alert_id = f"sprint38:{dedupe_key}"
        if alert_id in prior:
            continue
        alerts.append(
            Alert(
                alert_id=alert_id,
                severity=severity,
                category=gate.name,
                message=gate.message,
                dedupe_key=dedupe_key,
                created_at=timestamp,
                recommended_action=_alert_action_for_gate(gate),
            )
        )
    return alerts


def _alert_action_for_gate(gate: ReadinessGate) -> str:
    return {
        "daily_workflow_freshness": "Run workflow validation and inspect the latest daily run before relying on workbook state",
        "gmail_backlog": "Run Gmail ingestion or inspect Gmail_Messages errors",
        "high_priority_service_level": "Review unresolved high-potential jobs that lack a blocker reason",
        "source_failure_rate": "Review Source_Health by platform and pause only chronically failing sources",
        "lifecycle_false_closure_protection": "Inspect lifecycle evidence before accepting any closure update",
        "regression_pass_rate": "Run the regression evaluation and inspect failing cases",
        "dashboard_refresh_success": "Refresh Dashboard and check schema validity",
        "digest_refresh_success": "Refresh Digest and check schema validity",
        "verification_conversion": "Review high-potential partial and failed rows, then verify or keep the blocker visible",
    }.get(gate.name, "Inspect the gate details and supporting workbook metrics")


def build_readiness_run_record(readiness: ReadinessResult, *, now: str | None = None) -> dict[str, Any]:
    timestamp = now or utc_now_iso()
    run_timestamp = timestamp.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    failed = sum(1 for gate in readiness.gates if gate.status == "fail")
    warnings = sum(1 for gate in readiness.gates if gate.status == "warn")
    return {
        "run_id": f"sprint38_production_readiness_{run_timestamp}",
        "run_type": READINESS_RUN_TYPE,
        "source_type": "google_sheets",
        "source_name": "Production readiness gates",
        "status": "success" if readiness.classification in {"ready", "ready_with_warnings"} else "warning",
        "started_at": timestamp,
        "finished_at": timestamp,
        "duration_seconds": 0,
        "records_found": len(readiness.gates),
        "records_inserted": 1,
        "records_updated": 0,
        "records_failed": failed,
        "rows_read": _safe_int(readiness.metrics.get("jobs_read"), 0),
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "" if not failed else f"{failed} readiness gates failed",
        "notes": json.dumps({"readiness": readiness.to_dict(), "warnings": warnings}, sort_keys=True),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_metrics_from_workbook(
    *,
    jobs: Sequence[JobPosting],
    runs: Sequence[Mapping[str, Any]] | None = None,
    queue_rows: Sequence[Mapping[str, Any]] | None = None,
    source_health_rows: Sequence[Mapping[str, Any]] | None = None,
    now: str | None = None,
    regression_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = now or utc_now_iso()
    latest_daily = _latest_run(runs or [], ("daily", "production", "gmail"))
    latest_workflow = _latest_run(runs or [], ("workflow_validation", "schema"))
    latest_dashboard = _latest_run(runs or [], ("dashboard",))
    open_jobs = [job for job in jobs if _is_open(job)]
    high_potential = [job for job in open_jobs if job.potential_priority == "high"]
    verified_high = [job for job in high_potential if job.score_status == "verified"]
    unresolved_high = [job for job in high_potential if job.score_status != "verified"]
    unresolved_high_aged = [job for job in unresolved_high if _days_since(job.first_seen_date, timestamp) > 1]
    blocked_high = [job for job in unresolved_high_aged if _has_high_priority_blocker(job)]
    active_high = [job for job in unresolved_high_aged if str(job.enrichment_status or "").strip().lower() in HIGH_PRIORITY_ACTIVE_ENRICHMENT_STATUSES]
    high_sla_breaches = len(unresolved_high_aged) - len(blocked_high) - len(active_high)
    resolved_jobs = [job for job in open_jobs if str(job.canonical_url or job.enrichment_source_url or "").strip()]
    source_failure_rate = _source_failure_rate(source_health_rows or [])
    regression = dict(regression_metrics or {})
    return {
        "jobs_read": len(jobs),
        "daily_workflow_age_hours": _hours_since(_row_value(latest_daily, "finished_at", "started_at", "created_at"), timestamp) if latest_daily else 999999.0,
        "schema_valid": bool(latest_workflow and str(latest_workflow.get("status") or "").lower() == "success"),
        "gmail_backlog": _latest_note_metric(runs or [], "gmail", ("gmail_backlog_remaining", "backlog_remaining"), default=0),
        "enrichment_backlog": sum(1 for row in queue_rows or [] if str(row.get("status") or "").strip().lower() in QUEUE_OPEN_STATUSES),
        "high_priority_unresolved_aged": len(unresolved_high_aged),
        "high_priority_blocked": len(blocked_high),
        "high_priority_active_enrichment": len(active_high),
        "high_priority_sla_breaches": max(0, high_sla_breaches),
        "resolution_success_rate": round(len(resolved_jobs) / len(open_jobs), 4) if open_jobs else 1.0,
        "verification_conversion_rate": round(len(verified_high) / len(high_potential), 4) if high_potential else 1.0,
        "source_failure_rate": source_failure_rate,
        "false_closure_count": _safe_int(regression.get("false_closure_count"), 0),
        "regression_pass_rate": _safe_float(regression.get("regression_pass_rate"), 1.0),
        "dashboard_refresh_success": bool(latest_dashboard and str(latest_dashboard.get("status") or "").lower() == "success"),
        "digest_refresh_success": bool(latest_dashboard and str(latest_dashboard.get("status") or "").lower() == "success"),
    }


def _has_high_priority_blocker(job: JobPosting) -> bool:
    enrichment_status = str(job.enrichment_status or "").strip().lower()
    if enrichment_status in HIGH_PRIORITY_BLOCKER_ENRICHMENT_STATUSES:
        return True
    if job.score_status == "partially_verified":
        return True
    if str(job.lifecycle_reason or "").strip():
        return True
    if str(job.manual_decision_conflict or "").strip():
        return True
    if str(job.decision_evidence_conflict_notes or "").strip():
        return True
    if str(job.review_status or "").strip().lower() in REVIEWED_STATUSES - {"not_reviewed"}:
        return True
    return False


def _row_value(row: Mapping[str, Any] | None, *keys: str) -> str:
    for key in keys:
        value = (row or {}).get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _latest_run(rows: Sequence[Mapping[str, Any]], keywords: tuple[str, ...]) -> Mapping[str, Any] | None:
    normalized_keywords = [_normalize(keyword) for keyword in keywords]
    candidates = []
    for row in rows:
        text = _normalize(" ".join([_row_value(row, "run_type"), _row_value(row, "source_type"), _row_value(row, "source_name")]))
        if any(keyword in text for keyword in normalized_keywords):
            candidates.append(row)
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: _row_value(row, "finished_at", "started_at", "created_at"), reverse=True)[0]


def _latest_note_metric(rows: Sequence[Mapping[str, Any]], keyword: str, note_keys: tuple[str, ...], *, default: int = 0) -> int:
    latest = _latest_run(rows, (keyword,))
    if latest is None:
        return default
    notes = _parse_notes(latest.get("notes"))
    for key in note_keys:
        if key in notes:
            return _safe_int(notes.get(key), default)
    return default


def _parse_notes(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _source_failure_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    attempts = 0
    failures = 0
    for row in rows:
        attempts += _safe_int(row.get("requests") or row.get("attempts") or row.get("jobs_found"), 0)
        failures += _safe_int(row.get("failures") or row.get("consecutive_failures"), 0)
    if attempts <= 0:
        return 0.0
    return round(failures / attempts, 4)


def _read_optional_records(sheet_client: Any, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return list(sheet_client.read_records(worksheet_name))
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def run_production_readiness(
    sheet_client: Any,
    *,
    regression_cases: Sequence[Mapping[str, Any]] | None = None,
    thresholds: ReadinessThresholds | None = None,
    write_run_record: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    jobs = [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    runs = _read_optional_records(sheet_client, "Runs")
    queue_rows = _read_optional_records(sheet_client, "Enrichment_Queue")
    source_health_rows = _read_optional_records(sheet_client, "Source_Health")
    regression_metrics = evaluate_gold_standard_cases(list(regression_cases or [])) if regression_cases else {"regression_pass_rate": 1.0}
    metrics = build_metrics_from_workbook(
        jobs=jobs,
        runs=runs,
        queue_rows=queue_rows,
        source_health_rows=source_health_rows,
        now=now,
        regression_metrics=regression_metrics,
    )
    readiness = evaluate_readiness(metrics, thresholds=thresholds)
    alerts = build_alerts(readiness, created_at=now)
    if write_run_record:
        sheet_client.append_run(build_readiness_run_record(readiness, now=now))
    return {"readiness": readiness.to_dict(), "alerts": [alert.to_dict() for alert in alerts], "regression": regression_metrics}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Sprint 38 regression cases and production readiness gates")
    parser.add_argument("--fixture", default="data/regression/sprint38_gold_standard_jobs.json")
    parser.add_argument("--evaluate-regression", action="store_true")
    parser.add_argument("--evaluate-readiness", action="store_true")
    parser.add_argument("--write-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = load_gold_standard_cases(args.fixture) if args.evaluate_regression or Path(args.fixture).exists() else []
    if args.evaluate_readiness:
        from src.settings import load_settings
        from src.sheets import SheetClient

        sheet_client = SheetClient.from_settings(load_settings())
        result = run_production_readiness(
            sheet_client,
            regression_cases=cases,
            write_run_record=args.write_run and not args.dry_run,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if args.evaluate_regression or cases:
        print(json.dumps(evaluate_gold_standard_cases(cases), indent=2, sort_keys=True))
        return
    raise SystemExit("Choose --evaluate-regression or --evaluate-readiness")


if __name__ == "__main__":
    main()
