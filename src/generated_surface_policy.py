from __future__ import annotations

import re
from typing import Any

from src.models import JobPosting
from src.weekly_value import (
    _is_auto_rejected_job,
    _is_blocked_company_job,
    _is_too_senior_job,
)

TERMINAL_JOB_STATUSES = {"confirmed closed", "closed", "expired"}
TERMINAL_REVIEW_STATUSES = {"dismissed", "rejected", "withdrawn", "closed"}
TERMINAL_INTEREST_DECISIONS = {"dismissed", "not interested"}
TERMINAL_APPLICATION_STATUSES = {"rejected", "withdrawn", "closed"}
ACTIVE_APPLICATION_STATUSES = {
    "applied",
    "in review",
    "interviewing",
    "offer",
    "accepted",
}


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def has_active_application(job: JobPosting) -> bool:
    return normalize_status(job.application_status) in ACTIVE_APPLICATION_STATUSES


def is_terminal_job(job: JobPosting) -> bool:
    return normalize_status(job.status) in TERMINAL_JOB_STATUSES


def is_terminal_review(job: JobPosting) -> bool:
    return (
        normalize_status(job.review_status) in TERMINAL_REVIEW_STATUSES
        or normalize_status(job.interest_decision) in TERMINAL_INTEREST_DECISIONS
    )


def is_terminal_application(job: JobPosting) -> bool:
    return normalize_status(job.application_status) in TERMINAL_APPLICATION_STATUSES


def is_auto_rejected(job: JobPosting) -> bool:
    """Expose the canonical scoring rejection policy without private imports elsewhere."""
    return _is_auto_rejected_job(job)


def is_blocked_company(job: JobPosting) -> bool:
    """Return whether canonical scoring policy blocks the employer."""
    return _is_blocked_company_job(job)


def is_too_senior_hard_exclusion(job: JobPosting) -> bool:
    """Return whether the role is outside the supported seniority range."""
    return _is_too_senior_job(job)


def is_hard_excluded(job: JobPosting) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return (
        is_auto_rejected(job)
        or is_blocked_company(job)
        or is_too_senior_hard_exclusion(job)
        or normalize_status(job.score_status) == "excluded"
        or normalize_status(job.potential_priority) == "excluded"
        or "hard_exclude=true" in explanation
    )


def include_on_review_queue(job: JobPosting) -> bool:
    """Apply canonical suppressions before Review_Queue ranking."""

    if is_terminal_job(job) or is_terminal_review(job) or is_terminal_application(job):
        return False
    if is_hard_excluded(job):
        return False
    return True


def include_on_follow_up_queue(job: JobPosting) -> bool:
    """Keep active applications actionable while suppressing excluded leads."""

    if is_terminal_job(job) or is_terminal_application(job):
        return False
    if has_active_application(job):
        return True
    if is_terminal_review(job) or is_hard_excluded(job):
        return False
    return True


def include_in_current_context(job: JobPosting) -> bool:
    if is_terminal_job(job) or is_terminal_application(job):
        return False
    if has_active_application(job):
        return True
    if is_terminal_review(job) or is_hard_excluded(job):
        return False
    return True


def include_in_dashboard(job: JobPosting) -> bool:
    """Suppress rejected leads while retaining current applications and closures."""

    if is_terminal_application(job):
        return False
    if has_active_application(job):
        return True
    if is_terminal_review(job) or is_hard_excluded(job):
        return False
    return True
