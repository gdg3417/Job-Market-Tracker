from __future__ import annotations

import argparse
import json
import re
import socket
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Protocol
from urllib.parse import urlsplit

import requests

from src.models import utc_now_iso
from src.normalize import clean_text, normalize_url
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

SOURCE_AUDIT_SHEET = "Source_Audit"
SOURCE_YIELD_SHEET = "Source_Yield"
DEFAULT_WINDOW_WEEKS = 4
SUPPORTED_STRUCTURED_ATS = {
    "greenhouse": ("greenhouse.io", "boards.greenhouse.io"),
    "lever": ("lever.co", "jobs.lever.co"),
    "ashby": ("ashbyhq.com", "jobs.ashbyhq.com"),
    "smartrecruiters": ("smartrecruiters.com", "jobs.smartrecruiters.com"),
}

HEALTHY = "healthy"
EMPTY_VALID = "empty_but_valid"
REDIRECT_REQUIRED = "redirect_required"
STRUCTURED_ATS = "replaced_by_structured_ats"
TEMPORARILY_BLOCKED = "temporarily_blocked"
AUTH_OR_BOT_PROTECTION = "authentication_or_bot_protection"
PERMANENT_404 = "permanent_404_or_retired"
DNS_FAILURE = "dns_failure"
MANUAL_REVIEW = "manual_review_required"

AUDIT_CLASSIFICATIONS = {
    HEALTHY,
    EMPTY_VALID,
    REDIRECT_REQUIRED,
    STRUCTURED_ATS,
    TEMPORARILY_BLOCKED,
    AUTH_OR_BOT_PROTECTION,
    PERMANENT_404,
    DNS_FAILURE,
    MANUAL_REVIEW,
}

RETRYABLE_CLASSIFICATIONS = {
    EMPTY_VALID,
    TEMPORARILY_BLOCKED,
    DNS_FAILURE,
}

SOURCE_YIELD_HEADERS = [
    "window_start",
    "window_end",
    "group_type",
    "group_key",
    "source_type",
    "company",
    "strategic_target",
    "leads_received",
    "jobs_accepted",
    "auto_rejected",
    "blocked_company_rejects",
    "too_junior_rejects",
    "too_senior_rejects",
    "surfaced_for_review",
    "manually_dismissed",
    "interested",
    "applied",
    "strong_fit_count",
    "stretch_fit_count",
    "average_potential_score",
    "review_yield_percent",
    "actionable_conversion_percent",
    "recommendation",
    "recommendation_reason",
]

SOURCE_AUDIT_HEADERS = [
    "company_id",
    "company_name",
    "source_url",
    "final_url",
    "source_type",
    "ats_platform",
    "classification",
    "http_status",
    "retry_eligible",
    "retry_after",
    "requires_configuration_change",
    "failure_observations",
    "recommended_action",
    "recommendation_reason",
    "observed_at",
]


class ResponseLike(Protocol):
    status_code: int
    text: str
    url: str
    history: list[Any]
    headers: dict[str, Any]


class SessionLike(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout: int,
        headers: dict[str, str],
        allow_redirects: bool,
    ) -> ResponseLike:
        ...


@dataclass(frozen=True, slots=True)
class SourceProbe:
    source_url: str
    final_url: str
    classification: str
    http_status: int | None = None
    error_category: str = ""
    error_message: str = ""
    redirect_count: int = 0
    detected_ats: str = ""
    has_job_signal: bool = False
    observed_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry_eligible: bool
    retry_after: str = ""
    requires_configuration_change: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SourceAuditFinding:
    company_id: str
    company_name: str
    source_url: str
    final_url: str
    source_type: str
    ats_platform: str
    classification: str
    http_status: int | None
    retry_eligible: bool
    retry_after: str
    requires_configuration_change: bool
    failure_observations: int
    recommended_action: str
    recommendation_reason: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class YieldMetrics:
    leads_received: set[str] = field(default_factory=set)
    jobs_accepted: set[str] = field(default_factory=set)
    auto_rejected: set[str] = field(default_factory=set)
    blocked_company_rejects: set[str] = field(default_factory=set)
    too_junior_rejects: set[str] = field(default_factory=set)
    too_senior_rejects: set[str] = field(default_factory=set)
    surfaced_for_review: set[str] = field(default_factory=set)
    manually_dismissed: set[str] = field(default_factory=set)
    interested: set[str] = field(default_factory=set)
    applied: set[str] = field(default_factory=set)
    strong_fit: set[str] = field(default_factory=set)
    stretch_fit: set[str] = field(default_factory=set)
    potential_scores: list[float] = field(default_factory=list)
    source_types: set[str] = field(default_factory=set)
    companies: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class SourceYieldRow:
    window_start: str
    window_end: str
    group_type: str
    group_key: str
    source_type: str
    company: str
    strategic_target: bool
    leads_received: int
    jobs_accepted: int
    auto_rejected: int
    blocked_company_rejects: int
    too_junior_rejects: int
    too_senior_rejects: int
    surfaced_for_review: int
    manually_dismissed: int
    interested: int
    applied: int
    strong_fit_count: int
    stretch_fit_count: int
    average_potential_score: float
    review_yield_percent: float
    actionable_conversion_percent: float
    recommendation: str
    recommendation_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalized(value: Any) -> str:
    return clean_text(value).strip().lower().replace("-", "_").replace(" ", "_")


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _date_in_window(value: Any, start: date, end: date) -> bool:
    parsed = _parse_datetime(value)
    return parsed is not None and start <= parsed.date() <= end


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _percent(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _host(url: Any) -> str:
    try:
        return urlsplit(str(url or "")).netloc.lower().replace("www.", "")
    except ValueError:
        return ""


def detect_structured_ats(url: Any, content: Any = "") -> str:
    material = f"{str(url or '').lower()} {str(content or '').lower()}"
    for platform, host_terms in SUPPORTED_STRUCTURED_ATS.items():
        if any(term in material for term in host_terms):
            return platform
    if "greenhouse" in material:
        return "greenhouse"
    if "lever" in material:
        return "lever"
    if "ashby" in material:
        return "ashby"
    if "smartrecruiters" in material:
        return "smartrecruiters"
    return ""


def _has_job_signal(content: Any) -> bool:
    text = clean_text(content).lower()
    if not text:
        return False
    signals = (
        "jobposting",
        "job-title",
        "job title",
        "requisition",
        "open positions",
        "current openings",
        "view job",
        "apply now",
        "career opportunities",
    )
    return any(signal in text for signal in signals)


def _is_dns_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, socket.gaierror) or any(
        marker in text
        for marker in (
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "failed to resolve",
            "name resolution",
            "getaddrinfo",
        )
    )


def probe_source(
    source_url: str,
    *,
    session: SessionLike | None = None,
    timeout_seconds: int = 20,
    observed_at: str | None = None,
) -> SourceProbe:
    url = normalize_url(source_url)
    timestamp = observed_at or utc_now_iso()
    if not url:
        return SourceProbe(
            source_url="",
            final_url="",
            classification=MANUAL_REVIEW,
            error_category="invalid_configuration",
            error_message="Missing or invalid source URL",
            observed_at=timestamp,
        )

    client = session or requests
    try:
        response = client.get(
            url,
            timeout=timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 job-market-tracker source-quality-audit"},
            allow_redirects=True,
        )
    except requests.Timeout as exc:
        return SourceProbe(
            source_url=url,
            final_url=url,
            classification=TEMPORARILY_BLOCKED,
            error_category="timeout",
            error_message=str(exc),
            observed_at=timestamp,
        )
    except (requests.ConnectionError, socket.gaierror) as exc:
        classification = DNS_FAILURE if _is_dns_error(exc) else TEMPORARILY_BLOCKED
        return SourceProbe(
            source_url=url,
            final_url=url,
            classification=classification,
            error_category="dns_failure" if classification == DNS_FAILURE else "connection_failure",
            error_message=str(exc),
            observed_at=timestamp,
        )
    except requests.RequestException as exc:
        return SourceProbe(
            source_url=url,
            final_url=url,
            classification=TEMPORARILY_BLOCKED,
            error_category="request_failure",
            error_message=str(exc),
            observed_at=timestamp,
        )

    status = int(getattr(response, "status_code", 0) or 0)
    final_url = normalize_url(getattr(response, "url", "") or url)
    redirect_count = len(getattr(response, "history", []) or [])
    content = getattr(response, "text", "") or ""
    detected_ats = detect_structured_ats(final_url, content)
    job_signal = _has_job_signal(content)

    if status in {401, 407}:
        classification = AUTH_OR_BOT_PROTECTION
        category = "authentication_required"
    elif status == 403:
        classification = AUTH_OR_BOT_PROTECTION
        category = "blocked_or_bot_protection"
    elif status == 404:
        classification = PERMANENT_404
        category = "not_found"
    elif status == 410:
        classification = PERMANENT_404
        category = "retired"
    elif status == 429:
        classification = TEMPORARILY_BLOCKED
        category = "rate_limited"
    elif status >= 500:
        classification = TEMPORARILY_BLOCKED
        category = "temporary_server_failure"
    elif status >= 400:
        classification = MANUAL_REVIEW
        category = f"http_{status}"
    elif detected_ats:
        classification = STRUCTURED_ATS
        category = "structured_ats_detected"
    elif final_url and normalize_url(final_url) != normalize_url(url):
        classification = REDIRECT_REQUIRED
        category = "redirect"
    elif 200 <= status < 300 and job_signal:
        classification = HEALTHY
        category = "success"
    elif 200 <= status < 300:
        classification = EMPTY_VALID
        category = "empty_success"
    else:
        classification = MANUAL_REVIEW
        category = "unknown_response"

    return SourceProbe(
        source_url=url,
        final_url=final_url or url,
        classification=classification,
        http_status=status or None,
        error_category=category,
        error_message="",
        redirect_count=redirect_count,
        detected_ats=detected_ats,
        has_job_signal=job_signal,
        observed_at=timestamp,
    )


def retry_decision(
    probe: SourceProbe,
    *,
    failure_observations: int = 1,
    as_of: datetime | None = None,
) -> RetryDecision:
    now = (as_of or datetime.now(UTC)).astimezone(UTC)
    failures = max(1, int(failure_observations or 1))

    if probe.classification in {HEALTHY, STRUCTURED_ATS, REDIRECT_REQUIRED}:
        return RetryDecision(False, requires_configuration_change=probe.classification != HEALTHY, reason="Configuration action is required before another static fetch." if probe.classification != HEALTHY else "Source is healthy.")
    if probe.classification == PERMANENT_404:
        if failures < 2:
            return RetryDecision(
                True,
                (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
                False,
                "One 404 observation is insufficient for retirement. Retry after a conservative validation interval.",
            )
        return RetryDecision(
            False,
            "",
            True,
            "Repeated 404 or retired evidence requires a URL or configuration change before retry.",
        )
    if probe.classification == DNS_FAILURE:
        delay_days = 7 if failures < 3 else 30
        return RetryDecision(
            failures < 3,
            (now + timedelta(days=delay_days)).isoformat().replace("+00:00", "Z"),
            failures >= 3,
            "DNS failures use a bounded cooldown. Repeated failures require manual URL review.",
        )
    if probe.classification == AUTH_OR_BOT_PROTECTION:
        return RetryDecision(
            True,
            (now + timedelta(days=14)).isoformat().replace("+00:00", "Z"),
            False,
            "A blocked or protected response is temporary until corroborated. Do not retire the source from one observation.",
        )
    if probe.classification == TEMPORARILY_BLOCKED:
        delay = timedelta(hours=24) if probe.http_status == 429 else timedelta(days=1 if failures < 3 else 7)
        return RetryDecision(
            True,
            (now + delay).isoformat().replace("+00:00", "Z"),
            False,
            "Temporary failures remain recoverable and use a bounded cooldown.",
        )
    if probe.classification == EMPTY_VALID:
        return RetryDecision(
            True,
            (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
            False,
            "A valid empty source remains enabled but should run at a reduced cadence.",
        )
    return RetryDecision(False, "", True, "Manual review is required before retry.")


def _run_matches_source(run: dict[str, Any], company_name: str, source_url: str) -> bool:
    if _normalized(run.get("source_type")) != "static_page":
        return False
    material = " ".join(
        clean_text(run.get(field)).lower()
        for field in ("source_name", "notes", "error_message")
    )
    company = clean_text(company_name).lower()
    url = normalize_url(source_url).lower()
    return (company and company in material) or (url and url in material)


def prior_failure_observations(
    runs: Iterable[dict[str, Any]],
    *,
    company_name: str,
    source_url: str,
    classification: str,
    lookback_days: int = 90,
    as_of: datetime | None = None,
) -> int:
    now = (as_of or datetime.now(UTC)).astimezone(UTC)
    earliest = now - timedelta(days=max(1, lookback_days))
    count = 0
    for run in runs:
        if not _run_matches_source(run, company_name, source_url):
            continue
        observed = _parse_datetime(run.get("finished_at") or run.get("started_at") or run.get("created_at"))
        if observed is not None and observed < earliest:
            continue
        status = _normalized(run.get("status"))
        notes = clean_text(run.get("notes")).lower()
        error = clean_text(run.get("error_message")).lower()
        combined = f"{status} {notes} {error}"
        if classification == PERMANENT_404 and any(marker in combined for marker in ("404", "not found", "retired", "gone")):
            count += 1
        elif classification == DNS_FAILURE and any(marker in combined for marker in ("dns", "resolve", "getaddrinfo", "name resolution")):
            count += 1
        elif classification == AUTH_OR_BOT_PROTECTION and any(marker in combined for marker in ("403", "401", "blocked", "forbidden", "bot")):
            count += 1
        elif classification == TEMPORARILY_BLOCKED and status in {"failed", "partial_failure", "retryable_failure"}:
            count += 1
    return count


def _source_type(row: dict[str, Any]) -> str:
    return _normalized(row.get("source_type") or row.get("ingestion_mode") or row.get("ats_platform") or "static_page")


def _recommendation_for_probe(probe: SourceProbe, decision: RetryDecision) -> tuple[str, str]:
    if probe.classification == HEALTHY:
        return "keep", "Source responded successfully and exposed job signals."
    if probe.classification == EMPTY_VALID:
        return "reduce_cadence", "Source is reachable but currently produces no visible job signal."
    if probe.classification == REDIRECT_REQUIRED:
        return "replace_source_url", f"Update the configured URL to the validated canonical destination {probe.final_url}."
    if probe.classification == STRUCTURED_ATS:
        return "prefer_structured_ats", f"Use the {probe.detected_ats or 'detected'} structured ATS path instead of generic static scraping."
    if probe.classification == PERMANENT_404:
        action = "replace_or_retire_source" if decision.requires_configuration_change else "recheck_after_cooldown"
        return action, decision.reason
    if probe.classification == DNS_FAILURE:
        action = "manual_url_review" if decision.requires_configuration_change else "retry_after_cooldown"
        return action, decision.reason
    if probe.classification == AUTH_OR_BOT_PROTECTION:
        return "move_to_manual_review", decision.reason
    if probe.classification == TEMPORARILY_BLOCKED:
        return "retry_after_cooldown", decision.reason
    return "manual_review", decision.reason


def audit_static_sources(
    company_rows: Iterable[dict[str, Any]],
    *,
    runs: Iterable[dict[str, Any]] = (),
    session: SessionLike | None = None,
    probe_sources: bool = True,
    as_of: datetime | None = None,
) -> list[SourceAuditFinding]:
    now = (as_of or datetime.now(UTC)).astimezone(UTC)
    run_rows = list(runs)
    findings: list[SourceAuditFinding] = []

    for row in company_rows:
        if not _truthy(row.get("active"), default=True):
            continue
        source_url = normalize_url(row.get("source_url", ""))
        source_type = _source_type(row)
        source_material = " ".join(
            clean_text(row.get(field)).lower()
            for field in ("source_type", "ingestion_mode", "source_quality", "ats_platform", "source_url")
        )
        if not source_url and "gmail_only" in source_material:
            continue
        if not any(term in source_material for term in ("static", "career", "job", "workday", "icims", "oracle", "ashby", "smartrecruiters")):
            continue

        if probe_sources:
            probe = probe_source(source_url, session=session, observed_at=now.isoformat().replace("+00:00", "Z"))
        else:
            configured_ats = detect_structured_ats(source_url, row.get("ats_platform"))
            classification = STRUCTURED_ATS if configured_ats else MANUAL_REVIEW
            probe = SourceProbe(
                source_url=source_url,
                final_url=source_url,
                classification=classification,
                detected_ats=configured_ats,
                observed_at=now.isoformat().replace("+00:00", "Z"),
            )

        prior = prior_failure_observations(
            run_rows,
            company_name=clean_text(row.get("company_name")),
            source_url=source_url,
            classification=probe.classification,
            as_of=now,
        )
        failure_observations = prior + (1 if probe.classification not in {HEALTHY, EMPTY_VALID, REDIRECT_REQUIRED, STRUCTURED_ATS} else 0)
        decision = retry_decision(probe, failure_observations=max(1, failure_observations), as_of=now)
        action, reason = _recommendation_for_probe(probe, decision)
        findings.append(
            SourceAuditFinding(
                company_id=clean_text(row.get("company_id")),
                company_name=clean_text(row.get("company_name")),
                source_url=probe.source_url,
                final_url=probe.final_url,
                source_type=source_type,
                ats_platform=probe.detected_ats or clean_text(row.get("ats_platform")),
                classification=probe.classification,
                http_status=probe.http_status,
                retry_eligible=decision.retry_eligible,
                retry_after=decision.retry_after,
                requires_configuration_change=decision.requires_configuration_change,
                failure_observations=failure_observations,
                recommended_action=action,
                recommendation_reason=reason,
                observed_at=probe.observed_at,
            )
        )
    return findings


def apply_approved_source_updates(
    rows_with_numbers: Iterable[tuple[int, dict[str, Any]]],
    findings: Iterable[SourceAuditFinding],
    *,
    approved_company_ids: set[str],
    sheet_client: Any,
) -> list[dict[str, Any]]:
    approved = {clean_text(value).lower() for value in approved_company_ids if clean_text(value)}
    findings_by_company = {finding.company_id.lower(): finding for finding in findings if finding.company_id}
    updates: list[dict[str, Any]] = []

    for row_number, row in rows_with_numbers:
        company_id = clean_text(row.get("company_id")).lower()
        if company_id not in approved:
            continue
        finding = findings_by_company.get(company_id)
        if finding is None:
            continue

        updated = dict(row)
        if finding.classification == REDIRECT_REQUIRED and finding.final_url:
            updated["source_url"] = finding.final_url
            updated["source_quality"] = "success"
        elif finding.classification == STRUCTURED_ATS and finding.ats_platform in {"greenhouse", "lever"}:
            platform = finding.ats_platform
            updated["source_type"] = platform
            updated["ats_platform"] = platform
            updated["ingestion_mode"] = f"ats_{platform}"
            updated["source_quality"] = "success"
            updated["source_url"] = finding.final_url or finding.source_url
            updated["active"] = "TRUE"
        elif finding.classification == PERMANENT_404 and finding.requires_configuration_change and finding.failure_observations >= 2:
            updated["source_quality"] = "needs_manual_url_correction"
            updated["ingestion_mode"] = "manual_review_only"
            updated["active"] = "FALSE"
        else:
            continue

        marker = (
            f"Sprint 51 approved source update: classification={finding.classification}; "
            f"action={finding.recommended_action}; observed_at={finding.observed_at}"
        )
        existing_notes = clean_text(updated.get("notes"))
        if marker not in existing_notes:
            updated["notes"] = f"{existing_notes} | {marker}" if existing_notes else marker
        if updated != row:
            sheet_client.update_record("Config_Companies", row_number, updated)
            updates.append(
                {
                    "company_id": company_id,
                    "company_name": clean_text(row.get("company_name")),
                    "classification": finding.classification,
                    "action": finding.recommended_action,
                }
            )
    return updates


def _job_source_group_keys(source: dict[str, Any], job: dict[str, Any]) -> list[tuple[str, str]]:
    source_type = _normalized(source.get("source_type") or source.get("source_primary") or job.get("source_primary") or "unknown")
    source_url = normalize_url(source.get("source_url") or source.get("canonical_url") or job.get("canonical_url"))
    company = clean_text(job.get("company") or source.get("company")) or "Unknown company"
    primary = _normalized(source.get("source_primary") or job.get("source_primary"))
    keys = [
        ("source_type", source_type or "unknown"),
        ("company", company),
    ]
    if source_type == "static_page" or primary == "static_page":
        keys.append(("static_company_source", f"{company} | {source_url or 'unknown URL'}"))
    ats = detect_structured_ats(source_url, f"{source_type} {primary}")
    if ats:
        keys.append(("ats_platform", ats))
    if primary in {"gmail", "gmail_alert", "linkedin", "linkedin_email"} or source_type in {"gmail", "gmail_alert", "linkedin"}:
        keys.append(("gmail_alert_or_search", primary or source_type or _host(source_url) or "gmail"))
    return keys


def _rejected_group_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    source = _normalized(row.get("source") or row.get("sender") or "gmail_alert")
    company = clean_text(row.get("company")) or "Unknown company"
    subject = clean_text(row.get("subject"))
    keys = [("source_type", source), ("company", company)]
    if subject:
        keys.append(("gmail_alert_or_search", subject))
    else:
        keys.append(("gmail_alert_or_search", source))
    return keys


def _is_surfaced(job: dict[str, Any]) -> bool:
    status = _normalized(job.get("status"))
    review = _normalized(job.get("review_status"))
    interest = _normalized(job.get("interest_decision"))
    potential = _normalized(job.get("potential_priority"))
    score_status = _normalized(job.get("score_status"))
    if status in {"closed", "confirmed_closed", "expired"}:
        return False
    if review in {"dismissed", "rejected", "withdrawn", "closed"} or interest in {"dismissed", "not_interested"}:
        return False
    if potential == "excluded" or score_status == "excluded":
        return False
    return review in {"review_now", "reviewing", "interested", "watch", "deferred", "applied", "interviewing", "offer"} or potential in {"high", "medium"}


def _is_dismissed(job: dict[str, Any]) -> bool:
    return _normalized(job.get("review_status")) == "dismissed" or _normalized(job.get("interest_decision")) in {"dismissed", "not_interested"}


def _is_interested(job: dict[str, Any]) -> bool:
    return _normalized(job.get("interest_decision")) in {"interested", "applied"} or _normalized(job.get("review_status")) == "interested"


def _is_applied(job: dict[str, Any]) -> bool:
    states = {"applied", "interviewing", "offer"}
    return _normalized(job.get("application_status")) in states or _normalized(job.get("review_status")) in states


def _is_strong_fit(job: dict[str, Any]) -> bool:
    material = " ".join(
        clean_text(job.get(field)).lower()
        for field in ("verified_alert_tier", "alert_tier", "score_explanation", "potential_priority_reason")
    )
    if "strong fit" in material or "verified strong" in material:
        return True
    verified = _number(job.get("verified_total_score"))
    return verified >= 80


def _is_stretch_fit(job: dict[str, Any]) -> bool:
    material = " ".join(
        clean_text(job.get(field)).lower()
        for field in ("title", "score_explanation", "potential_priority_reason", "review_notes")
    )
    return "stretch fit" in material or "stretch_fit" in material


def _reason_flags(row: dict[str, Any]) -> tuple[bool, bool, bool]:
    material = " ".join(
        clean_text(row.get(field)).lower()
        for field in ("rejection_reason", "extraction_notes", "raw_evidence", "title", "company")
    )
    blocked = any(marker in material for marker in ("blocked_company", "blocked company", "company excluded", "consulting firm"))
    junior = any(marker in material for marker in ("too_junior", "too junior", "role_too_junior", "entry level", "analyst"))
    senior = any(marker in material for marker in ("too_senior", "too senior", "role_too_senior", "vice president", "senior director"))
    return blocked, junior, senior


def _strategic_companies(target_company_rows: Iterable[dict[str, Any]]) -> set[str]:
    return {
        clean_text(row.get("company_name")).lower()
        for row in target_company_rows
        if _truthy(row.get("active"), default=True) and clean_text(row.get("company_name"))
    }


def _yield_recommendation(
    *,
    metrics: YieldMetrics,
    strategic_target: bool,
) -> tuple[str, str]:
    leads = len(metrics.leads_received)
    accepted = len(metrics.jobs_accepted)
    rejected = len(metrics.auto_rejected)
    surfaced = len(metrics.surfaced_for_review)
    positive = len(metrics.interested | metrics.applied)
    too_senior = len(metrics.too_senior_rejects)
    too_junior = len(metrics.too_junior_rejects)
    blocked = len(metrics.blocked_company_rejects)

    if strategic_target:
        if leads == 0:
            return "keep_strategic_coverage", "The source covers a strategic target company even though the current window has no leads."
        return "keep", "Strategic target-company coverage is retained unless a replacement source is validated."
    if leads == 0:
        return "review_or_reduce_cadence", "No leads were observed in the reporting window. Confirm the source still adds coverage before retiring it."
    if leads >= 10 and blocked / leads >= 0.5:
        return "narrow_or_retire", "At least half of observed leads were blocked-company rejects."
    if leads >= 10 and (too_senior + too_junior) / leads >= 0.5:
        return "narrow_search", "At least half of observed leads missed the supported seniority range."
    if leads >= 20 and positive / max(1, surfaced) < 0.05:
        return "reduce_cadence", "The source generated substantial review volume with less than five percent positive review yield."
    if rejected > accepted and leads >= 10:
        return "narrow_search", "Rejected leads exceeded accepted jobs during the reporting window."
    if surfaced == 0 and accepted > 0:
        return "review_filtering", "Jobs were accepted but none surfaced for review. Inspect search terms and exclusion logic."
    return "keep", "The source is producing usable volume without a clear low-yield signal."


def build_source_yield_report(
    *,
    jobs: Iterable[dict[str, Any]],
    job_sources: Iterable[dict[str, Any]],
    rejected_jobs: Iterable[dict[str, Any]],
    target_companies: Iterable[dict[str, Any]] = (),
    weeks: int = DEFAULT_WINDOW_WEEKS,
    as_of: date | None = None,
) -> list[SourceYieldRow]:
    end = as_of or datetime.now(UTC).date()
    start = end - timedelta(days=max(1, int(weeks or DEFAULT_WINDOW_WEEKS)) * 7 - 1)
    jobs_by_key = {clean_text(row.get("job_key")): dict(row) for row in jobs if clean_text(row.get("job_key"))}
    strategic = _strategic_companies(target_companies)
    grouped: dict[tuple[str, str], YieldMetrics] = defaultdict(YieldMetrics)

    for source in job_sources:
        if not _date_in_window(source.get("first_seen_date") or source.get("created_at"), start, end):
            continue
        job_key = clean_text(source.get("job_key"))
        job = jobs_by_key.get(job_key, {})
        if not job_key:
            continue
        for group in _job_source_group_keys(source, job):
            metrics = grouped[group]
            metrics.leads_received.add(job_key)
            metrics.jobs_accepted.add(job_key)
            metrics.source_types.add(_normalized(source.get("source_type") or source.get("source_primary") or job.get("source_primary") or "unknown"))
            company = clean_text(job.get("company") or source.get("company"))
            if company:
                metrics.companies.add(company)
            if _is_surfaced(job):
                metrics.surfaced_for_review.add(job_key)
            if _is_dismissed(job):
                metrics.manually_dismissed.add(job_key)
            if _is_interested(job):
                metrics.interested.add(job_key)
            if _is_applied(job):
                metrics.applied.add(job_key)
            if _is_strong_fit(job):
                metrics.strong_fit.add(job_key)
            if _is_stretch_fit(job):
                metrics.stretch_fit.add(job_key)
            score = _number(job.get("potential_priority_score"))
            if score:
                metrics.potential_scores.append(score)

    for index, rejected in enumerate(rejected_jobs):
        if not _date_in_window(rejected.get("received_date") or rejected.get("created_at"), start, end):
            continue
        rejected_id = clean_text(rejected.get("rejected_id")) or f"rejected_{index}"
        blocked, junior, senior = _reason_flags(rejected)
        for group in _rejected_group_keys(rejected):
            metrics = grouped[group]
            metrics.leads_received.add(rejected_id)
            metrics.auto_rejected.add(rejected_id)
            metrics.source_types.add(_normalized(rejected.get("source") or rejected.get("sender") or "gmail_alert"))
            company = clean_text(rejected.get("company"))
            if company:
                metrics.companies.add(company)
            if blocked:
                metrics.blocked_company_rejects.add(rejected_id)
            if junior:
                metrics.too_junior_rejects.add(rejected_id)
            if senior:
                metrics.too_senior_rejects.add(rejected_id)

    rows: list[SourceYieldRow] = []
    for (group_type, group_key), metrics in grouped.items():
        companies = sorted(metrics.companies)
        company = companies[0] if len(companies) == 1 else "Multiple" if companies else ""
        strategic_target = any(name.lower() in strategic for name in companies)
        recommendation, reason = _yield_recommendation(metrics=metrics, strategic_target=strategic_target)
        positive = len(metrics.interested | metrics.applied)
        average_score = round(sum(metrics.potential_scores) / len(metrics.potential_scores), 1) if metrics.potential_scores else 0.0
        rows.append(
            SourceYieldRow(
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                group_type=group_type,
                group_key=group_key,
                source_type=", ".join(sorted(value for value in metrics.source_types if value)),
                company=company,
                strategic_target=strategic_target,
                leads_received=len(metrics.leads_received),
                jobs_accepted=len(metrics.jobs_accepted),
                auto_rejected=len(metrics.auto_rejected),
                blocked_company_rejects=len(metrics.blocked_company_rejects),
                too_junior_rejects=len(metrics.too_junior_rejects),
                too_senior_rejects=len(metrics.too_senior_rejects),
                surfaced_for_review=len(metrics.surfaced_for_review),
                manually_dismissed=len(metrics.manually_dismissed),
                interested=len(metrics.interested),
                applied=len(metrics.applied),
                strong_fit_count=len(metrics.strong_fit),
                stretch_fit_count=len(metrics.stretch_fit),
                average_potential_score=average_score,
                review_yield_percent=_percent(positive, len(metrics.surfaced_for_review)),
                actionable_conversion_percent=_percent(positive, len(metrics.leads_received)),
                recommendation=recommendation,
                recommendation_reason=reason,
            )
        )

    return sorted(rows, key=lambda row: (row.group_type, -row.leads_received, row.group_key.lower()))


def _replace_generated_sheet(sheet_client: Any, worksheet_name: str, headers: list[str], records: list[dict[str, Any]]) -> int:
    worksheet = sheet_client.ensure_worksheet(
        worksheet_name,
        rows=max(100, len(records) + 10),
        cols=max(10, len(headers)),
    )
    rows = [headers] + [[record.get(header, "") for header in headers] for record in records]
    if hasattr(worksheet, "resize"):
        with_quota_backoff(
            lambda: worksheet.resize(rows=max(100, len(rows) + 5), cols=max(len(headers), 10)),
            operation_name=f"resize generated worksheet {worksheet_name}",
        )
    with_quota_backoff(
        lambda: worksheet.clear(),
        operation_name=f"clear generated worksheet {worksheet_name}",
    )
    with_quota_backoff(
        lambda: worksheet.update(range_name="A1", values=rows, value_input_option="USER_ENTERED"),
        operation_name=f"write generated worksheet {worksheet_name}",
    )
    return len(records)


def write_source_quality_surfaces(
    sheet_client: Any,
    *,
    findings: Iterable[SourceAuditFinding],
    yield_rows: Iterable[SourceYieldRow],
) -> dict[str, int]:
    finding_records = [finding.to_dict() for finding in findings]
    yield_records = [row.to_dict() for row in yield_rows]
    return {
        "source_audit_rows_written": _replace_generated_sheet(sheet_client, SOURCE_AUDIT_SHEET, SOURCE_AUDIT_HEADERS, finding_records),
        "source_yield_rows_written": _replace_generated_sheet(sheet_client, SOURCE_YIELD_SHEET, SOURCE_YIELD_HEADERS, yield_records),
    }


def build_run_record(
    *,
    findings: list[SourceAuditFinding],
    yield_rows: list[SourceYieldRow],
    updates: list[dict[str, Any]],
    weeks: int,
) -> dict[str, Any]:
    now = utc_now_iso()
    timestamp = now.replace(":", "").replace("-", "").replace("+00:00", "Z")
    issue_count = len([finding for finding in findings if finding.classification != HEALTHY])
    summary = {
        "window_weeks": weeks,
        "sources_audited": len(findings),
        "source_issues": issue_count,
        "yield_rows": len(yield_rows),
        "approved_updates": updates,
        "classification_counts": {
            classification: len([finding for finding in findings if finding.classification == classification])
            for classification in sorted(AUDIT_CLASSIFICATIONS)
        },
    }
    return {
        "run_id": f"sprint51_source_quality_{timestamp}",
        "run_type": "sprint_51_source_quality_yield",
        "source_type": "source_quality",
        "source_name": "Source quality and four-week yield",
        "status": "success" if issue_count == 0 else "review_recommended",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": len(findings) + len(yield_rows),
        "records_inserted": 0,
        "records_updated": len(updates),
        "records_failed": 0,
        "rows_read": 0,
        "config_companies_rows": len(findings),
        "config_searches_rows": 0,
        "companies_read": len(findings),
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def run_source_quality(
    *,
    weeks: int = DEFAULT_WINDOW_WEEKS,
    probe_sources: bool = True,
    write_report: bool = False,
    approved_company_ids: set[str] | None = None,
    sheet_client: Any | None = None,
) -> dict[str, Any]:
    client = sheet_client or SheetClient.from_settings(load_settings())
    company_rows_with_numbers = client.read_records_with_row_numbers("Config_Companies")
    company_rows = [row for _, row in company_rows_with_numbers]
    runs = client.read_records("Runs")
    jobs = client.read_records("Jobs")
    job_sources = client.read_records("Job_Sources")
    rejected_jobs = client.read_records("Rejected_Jobs")
    target_companies = client.read_records("Target_Companies")

    findings = audit_static_sources(
        company_rows,
        runs=runs,
        probe_sources=probe_sources,
    )
    yield_rows = build_source_yield_report(
        jobs=jobs,
        job_sources=job_sources,
        rejected_jobs=rejected_jobs,
        target_companies=target_companies,
        weeks=weeks,
    )
    updates: list[dict[str, Any]] = []
    if approved_company_ids:
        updates = apply_approved_source_updates(
            company_rows_with_numbers,
            findings,
            approved_company_ids=approved_company_ids,
            sheet_client=client,
        )

    writes = {"source_audit_rows_written": 0, "source_yield_rows_written": 0}
    if write_report:
        writes = write_source_quality_surfaces(client, findings=findings, yield_rows=yield_rows)
        client.append_run(build_run_record(findings=findings, yield_rows=yield_rows, updates=updates, weeks=weeks))

    classification_counts = {
        classification: len([finding for finding in findings if finding.classification == classification])
        for classification in sorted(AUDIT_CLASSIFICATIONS)
    }
    recommendation_counts: dict[str, int] = defaultdict(int)
    for row in yield_rows:
        recommendation_counts[row.recommendation] += 1

    return {
        "status": "success",
        "weeks": weeks,
        "probe_sources": probe_sources,
        "sources_audited": len(findings),
        "classification_counts": classification_counts,
        "configuration_changes_required": len([finding for finding in findings if finding.requires_configuration_change]),
        "retryable_sources": len([finding for finding in findings if finding.retry_eligible]),
        "yield_rows": len(yield_rows),
        "yield_recommendation_counts": dict(sorted(recommendation_counts.items())),
        "approved_updates": updates,
        **writes,
        "findings": [finding.to_dict() for finding in findings],
        "source_yield": [row.to_dict() for row in yield_rows],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit source quality and generate a configurable source-yield report")
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--dry-run", action="store_true", help="Calculate output without writing generated sheets")
    execution.add_argument("--write-report", action="store_true", help="Write Source_Audit and Source_Yield generated sheets")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WINDOW_WEEKS, help="Reporting window in weeks")
    parser.add_argument("--skip-live-probes", action="store_true", help="Use configuration-only audit without network probes")
    parser.add_argument(
        "--approved-company-id",
        action="append",
        default=[],
        help="Explicit company_id approved for a supported configuration update. May be repeated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    approved = {clean_text(value) for value in args.approved_company_id if clean_text(value)}
    if approved and not args.write_report:
        raise SystemExit("Approved configuration updates require --write-report so the audit evidence is persisted.")
    result = run_source_quality(
        weeks=max(1, args.weeks),
        probe_sources=not args.skip_live_probes,
        write_report=args.write_report,
        approved_company_ids=approved,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
