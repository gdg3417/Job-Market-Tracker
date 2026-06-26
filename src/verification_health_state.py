from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from src.verification_health_models import (
    APPLICATION_STATUSES,
    DISMISSED_STATUSES,
    OPEN_STATUSES,
    REVIEWED_STATUSES,
    TERMINAL_STATUSES,
    HealthThresholds,
    identity,
    parse_datetime,
    row_timestamp,
    safe_int,
)

KNOWN_ATS_HOST_TERMS = (
    "myworkdayjobs.com", "greenhouse.io", "lever.co", "icims.com",
    "smartrecruiters.com", "successfactors.com", "oraclecloud.com",
    "jobvite.com", "phenompeople.com",
)
UNTRUSTED_JOB_BOARD_HOSTS = {
    "linkedin.com", "www.linkedin.com", "indeed.com", "www.indeed.com",
    "ziprecruiter.com", "www.ziprecruiter.com", "glassdoor.com", "www.glassdoor.com",
}


def is_open(row: dict[str, Any]) -> bool:
    return identity(row.get("status") or "open") in OPEN_STATUSES


def is_excluded(row: dict[str, Any]) -> bool:
    return identity(row.get("score_status")) == "excluded" or identity(row.get("potential_priority")) == "excluded"


def is_high(row: dict[str, Any]) -> bool:
    return identity(row.get("potential_priority")) == "high"


def is_medium_signal(row: dict[str, Any]) -> bool:
    title = identity(row.get("title"))
    terms = ("director", "senior manager", "sr manager", "national manager", "strategy", "operations", "product")
    return identity(row.get("potential_priority")) == "medium" and any(term in title for term in terms)


def is_verified(row: dict[str, Any]) -> bool:
    return identity(row.get("score_status")) == "verified"


def is_partial(row: dict[str, Any]) -> bool:
    return identity(row.get("score_status")) == "partially verified"


def is_reviewed(row: dict[str, Any]) -> bool:
    return identity(row.get("review_status")) in REVIEWED_STATUSES or bool(row_timestamp(row, "reviewed_date", "reviewed_at"))


def is_applied(row: dict[str, Any]) -> bool:
    return (
        identity(row.get("application_status")) in APPLICATION_STATUSES
        or identity(row.get("review_status")) in APPLICATION_STATUSES
        or bool(row_timestamp(row, "application_date"))
    )


def is_dismissed(row: dict[str, Any]) -> bool:
    return identity(row.get("review_status")) in DISMISSED_STATUSES or bool(str(row.get("dismissal_reason") or "").strip())


def is_deferred(row: dict[str, Any], as_of: datetime) -> bool:
    if identity(row.get("review_status")) != "deferred":
        return False
    follow_up = parse_datetime(row_timestamp(row, "follow_up_date", "next_action_date"), end_of_day=True)
    return follow_up is None or follow_up > as_of


def is_closed(row: dict[str, Any]) -> bool:
    return identity(row.get("status")) in TERMINAL_STATUSES


def job_timestamp(row: dict[str, Any]) -> str:
    return row_timestamp(row, "created_at", "first_seen_date", "received_at", "received_date", "updated_at")


def latest_by_job(rows: list[dict[str, Any]], *timestamp_fields: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("job_key") or "").strip()
        if not key:
            continue
        current = selected.get(key)
        if current is None or row_timestamp(row, *timestamp_fields) >= row_timestamp(current, *timestamp_fields):
            selected[key] = row
    return selected


def parse_notes(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    notes = row.get("notes")
    if isinstance(notes, dict):
        return notes
    try:
        parsed = json.loads(str(notes or ""))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def latest_run(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    matches = [row for row in rows if predicate(row)]
    return max(matches, key=lambda row: row_timestamp(row, "finished_at", "started_at", "created_at"), default=None)


def daily_run(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return latest_run(rows, lambda row: "daily" in identity(row.get("run_type")) or "daily" in identity(row.get("source_name")))


def _host(url: Any) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def authoritative(job: dict[str, Any], queue: dict[str, Any] | None, thresholds: HealthThresholds) -> bool:
    queue = queue or {}
    url = str(queue.get("matched_url") or job.get("enrichment_source_url") or job.get("canonical_url") or "").strip()
    if not url:
        return False
    confidence = safe_int(queue.get("match_confidence") or job.get("enrichment_match_confidence"), 100 if is_verified(job) else 0)
    source = identity(queue.get("current_stage") or job.get("enrichment_source_type"))
    host = _host(url)
    source_is_authoritative = "company" in source or "ats" in source
    known_ats = any(term in host for term in KNOWN_ATS_HOST_TERMS)
    employer_like = bool(host and host not in UNTRUSTED_JOB_BOARD_HOSTS and not host.endswith("linkedin.com"))
    return confidence >= thresholds.authoritative_match_min_confidence and (
        is_verified(job) or source_is_authoritative or known_ats or employer_like
    )


def _add_company_keys(keys: set[str], row: dict[str, Any]) -> None:
    for field in ("company_name", "canonical_company_name", "parent_company"):
        value = identity(row.get(field))
        if value:
            keys.add(value)
    for alias in str(row.get("company_aliases") or "").replace(";", ",").split(","):
        normalized = identity(alias)
        if normalized:
            keys.add(normalized)


def target_keys(target_rows: list[dict[str, Any]], config_rows: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in target_rows:
        if str(row.get("active", "TRUE")).strip().lower() not in {"false", "0", "no"}:
            _add_company_keys(keys, row)
    for row in config_rows:
        if str(row.get("active", "TRUE")).strip().lower() in {"false", "0", "no"}:
            continue
        tier = identity(row.get("priority_tier"))
        if tier in {"tier 1", "tier 2", "1", "2", "high", "medium"}:
            _add_company_keys(keys, row)
    return keys


def is_target(job: dict[str, Any], keys: set[str]) -> bool:
    company = identity(job.get("company"))
    return bool(company and any(company == key or company in key or key in company for key in keys))
