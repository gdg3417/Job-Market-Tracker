from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.generated_surface_policy import (
    has_active_application,
    is_hard_excluded,
    is_terminal_application,
    is_terminal_job,
    is_terminal_review,
    normalize_status,
)
from src.sheet_dates import normalize_record_dates, normalize_sheet_date, normalized_job_from_record
from src.verification_health_models import parse_datetime
from src.weekly_value import (
    _is_auto_rejected_job,
    _is_blocked_company_job,
    _is_too_senior_job,
)

CENTRAL_TIMEZONE = ZoneInfo("America/Chicago")
DEFERRED_STATUSES = {"deferred"}
LIKELY_CLOSED_STATUSES = {"likely closed", "not seen once"}


@dataclass(frozen=True, slots=True)
class Actionability:
    actionable: bool
    reason: str
    detail: str = ""


def has_valid_job_identity(row: dict[str, Any]) -> bool:
    """Require the canonical key and the minimum human-readable identity."""
    return all(
        str(row.get(field) or "").strip()
        for field in ("job_key", "company", "title")
    )


def _due_date_value(row: dict[str, Any]) -> Any:
    return row.get("follow_up_date") or row.get("next_action_date")


def _deferred_state(row: dict[str, Any], as_of: datetime) -> Actionability | None:
    job = normalized_job_from_record(row)
    review_status = normalize_status(job.review_status)
    interest_decision = normalize_status(job.interest_decision)
    if review_status not in DEFERRED_STATUSES and interest_decision not in DEFERRED_STATUSES:
        return None

    raw_due = normalize_sheet_date(_due_date_value(row))
    if raw_due in (None, ""):
        return Actionability(
            True,
            "deferred_missing_due_date",
            "Deferred role has no follow-up date and requires manual correction.",
        )

    due = parse_datetime(raw_due, end_of_day=True)
    if due is None:
        return Actionability(
            True,
            "deferred_invalid_due_date",
            "Deferred role has an unreadable follow-up date and requires manual correction.",
        )

    local_as_of = as_of.astimezone(CENTRAL_TIMEZONE)
    local_due = due.astimezone(CENTRAL_TIMEZONE)
    if local_due.date() > local_as_of.date():
        return Actionability(
            False,
            "deferred_not_due",
            f"Deferred until {local_due.date().isoformat()}.",
        )
    return Actionability(
        True,
        "deferred_due",
        f"Deferred follow-up was due on {local_due.date().isoformat()}.",
    )


def classify_actionability(row: dict[str, Any], *, as_of: datetime) -> Actionability:
    """Classify whether a canonical Jobs row can still affect a current decision."""
    if not has_valid_job_identity(row):
        return Actionability(False, "invalid_job_identity", "Missing job key, company, or title.")

    job = normalized_job_from_record(row)

    if is_terminal_job(job):
        return Actionability(False, "terminal_job", f"Job status is {normalize_status(job.status)}.")
    if is_terminal_application(job):
        return Actionability(
            False,
            "terminal_application",
            f"Application status is {normalize_status(job.application_status)}.",
        )
    if _is_blocked_company_job(job):
        return Actionability(False, "blocked_company", "Company is blocked by canonical policy.")
    if _is_too_senior_job(job):
        return Actionability(False, "too_senior_hard_exclusion", "Role is a hard seniority exclusion.")
    if _is_auto_rejected_job(job) or is_hard_excluded(job):
        return Actionability(False, "hard_excluded", "Role is excluded by canonical scoring policy.")
    if is_terminal_review(job):
        review = normalize_status(job.review_status)
        interest = normalize_status(job.interest_decision)
        if review == "dismissed" or interest in {"dismissed", "not interested"}:
            return Actionability(False, "dismissed", "Role was manually dismissed.")
        return Actionability(False, "terminal_review", f"Review status is {review or interest}.")

    deferred = _deferred_state(row, as_of)
    if deferred is not None:
        return deferred

    if has_active_application(job):
        return Actionability(True, "active_application", "Application remains active.")
    if normalize_status(job.status) in LIKELY_CLOSED_STATUSES:
        return Actionability(
            True,
            "closure_confirmation_required",
            "Posting is likely closed but has not been authoritatively closed.",
        )
    if normalize_status(job.review_status) in {"interested", "watch", "reviewing", "review now"}:
        return Actionability(True, "human_decision_pending", "Human review or decision remains active.")
    return Actionability(True, "open_role", "Role remains eligible for current action.")


def partition_actionable_jobs(
    rows: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Actionability], dict[str, int]]:
    actionable: list[dict[str, Any]] = []
    classifications: dict[str, Actionability] = {}
    excluded_counts: Counter[str] = Counter()

    for index, row in enumerate(rows):
        result = classify_actionability(row, as_of=as_of)
        key = str(row.get("job_key") or "").strip() or f"__invalid_row_{index}"
        classifications[key] = result
        if result.actionable:
            actionable.append(normalize_record_dates(row))
        else:
            excluded_counts[result.reason] += 1

    return actionable, classifications, dict(sorted(excluded_counts.items()))


def is_deferred_due_or_invalid(row: dict[str, Any], *, as_of: datetime) -> bool:
    result = classify_actionability(row, as_of=as_of)
    return result.actionable and result.reason in {
        "deferred_due",
        "deferred_missing_due_date",
        "deferred_invalid_due_date",
    }
