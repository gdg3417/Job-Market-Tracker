from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.models import (
    JobPosting,
    VALID_DISMISSAL_REASONS,
    VALID_REVIEW_STATUSES,
    parse_iso_date,
    today_iso,
)

REVIEW_STATUS_RANK = {
    "not_reviewed": 0,
    "review_now": 1,
    "reviewing": 2,
    "watch": 3,
    "deferred": 3,
    "interested": 4,
    "dismissed": 5,
    "applied": 6,
    "interviewing": 7,
    "offer": 8,
    "rejected": 9,
    "withdrawn": 9,
    "closed": 10,
}

APPLICATION_STATUS_RANK = {
    "": 0,
    "not_started": 0,
    "drafting": 1,
    "applied": 2,
    "interviewing": 3,
    "offer": 4,
    "rejected": 5,
    "withdrawn": 5,
    "closed": 6,
}

REVIEW_TRANSITIONS = {
    "not_reviewed": {"review_now", "reviewing", "interested", "watch", "deferred", "dismissed", "applied", "closed"},
    "review_now": {"reviewing", "interested", "watch", "deferred", "dismissed", "applied", "closed"},
    "reviewing": {"review_now", "interested", "watch", "deferred", "dismissed", "applied", "closed"},
    "interested": {"reviewing", "watch", "deferred", "dismissed", "applied", "closed"},
    "watch": {"review_now", "reviewing", "interested", "deferred", "dismissed", "applied", "closed"},
    "deferred": {"review_now", "reviewing", "interested", "watch", "dismissed", "applied", "closed"},
    "dismissed": {"review_now", "reviewing", "closed"},
    "applied": {"interviewing", "offer", "rejected", "withdrawn", "closed"},
    "interviewing": {"applied", "offer", "rejected", "withdrawn", "closed"},
    "offer": {"interviewing", "rejected", "withdrawn", "closed"},
    "rejected": {"closed", "review_now"},
    "withdrawn": {"closed", "review_now"},
    "closed": {"review_now"},
}

APPLICATION_REVIEW_STATUSES = {"applied", "interviewing", "offer", "rejected", "withdrawn", "closed"}


def normalize_review_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in VALID_REVIEW_STATUSES else "not_reviewed"


def validate_review_transition(current_status: Any, next_status: Any) -> None:
    current = normalize_review_status(current_status)
    next_value = normalize_review_status(next_status)
    if current == next_value:
        return
    allowed = REVIEW_TRANSITIONS.get(current, set())
    if next_value not in allowed:
        raise ValueError(f"Invalid review transition from {current} to {next_value}")


def _copy_job(job: JobPosting) -> JobPosting:
    return JobPosting.from_dict(job.to_dict())


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _date_min(*values: Any) -> str:
    parsed = [(parse_iso_date(value), str(value or "")) for value in values if value not in (None, "")]
    parsed = [(date_value, original) for date_value, original in parsed if date_value is not None]
    if not parsed:
        return ""
    return sorted(parsed, key=lambda item: item[0])[0][1]


def _date_max(*values: Any) -> str:
    parsed = [(parse_iso_date(value), str(value or "")) for value in values if value not in (None, "")]
    parsed = [(date_value, original) for date_value, original in parsed if date_value is not None]
    if not parsed:
        return ""
    return sorted(parsed, key=lambda item: item[0], reverse=True)[0][1]


def apply_review_update(job: JobPosting, **updates: Any) -> JobPosting:
    updated = _copy_job(job)
    if "review_status" in updates:
        next_status = normalize_review_status(updates["review_status"])
        validate_review_transition(updated.review_status, next_status)
        updated.review_status = next_status
        if next_status != "not_reviewed" and not updated.reviewed_date:
            updated.reviewed_date = str(updates.get("reviewed_date") or today_iso())
    for field_name, value in updates.items():
        if field_name == "review_status":
            continue
        if not hasattr(updated, field_name):
            raise ValueError(f"Unknown review field: {field_name}")
        if field_name == "dismissal_reason":
            reason = str(value or "").strip().lower()
            if reason not in VALID_DISMISSAL_REASONS:
                raise ValueError(f"Invalid dismissal reason: {reason}")
            setattr(updated, field_name, reason)
            continue
        if field_name == "manual_priority" and value in (None, ""):
            setattr(updated, field_name, None)
            continue
        setattr(updated, field_name, value)
    if updated.review_status == "dismissed" and not updated.dismissal_reason:
        updated.dismissal_reason = "other"
    if updated.review_status in APPLICATION_REVIEW_STATUSES and not updated.application_status:
        updated.application_status = updated.review_status
    if updated.review_status == "applied" and not updated.application_date:
        updated.application_date = str(updates.get("application_date") or today_iso())
    updated.refresh_updated_at()
    return JobPosting.from_dict(updated.to_dict())


def manual_priority_sort_key(job: JobPosting) -> tuple[int, int, int, int, str]:
    manual_priority = job.manual_priority if job.manual_priority is not None else -1
    verified_score = job.verified_total_score if job.verified_total_score is not None else job.total_score
    return (
        manual_priority,
        REVIEW_STATUS_RANK.get(job.review_status, 0),
        verified_score,
        job.potential_priority_score,
        job.last_seen_date,
    )


def sorted_for_action_queue(jobs: list[JobPosting]) -> list[JobPosting]:
    return sorted(jobs, key=manual_priority_sort_key, reverse=True)


def _merge_text(existing_value: str, incoming_value: str) -> str:
    existing_text = str(existing_value or "").strip()
    incoming_text = str(incoming_value or "").strip()
    if not existing_text:
        return incoming_text
    if not incoming_text or incoming_text == existing_text:
        return existing_text
    return f"{existing_text}\n{incoming_text}"


def _review_conflict(existing: JobPosting, incoming: JobPosting) -> str:
    existing_status = normalize_review_status(existing.review_status)
    incoming_status = normalize_review_status(incoming.review_status)
    if existing_status in {"dismissed", "withdrawn", "rejected", "closed"} and incoming_status in {"interested", "applied", "interviewing", "offer"}:
        return f"conflicting_manual_decisions:{existing_status}_vs_{incoming_status}"
    if incoming_status in {"dismissed", "withdrawn", "rejected", "closed"} and existing_status in {"interested", "applied", "interviewing", "offer"}:
        return f"conflicting_manual_decisions:{existing_status}_vs_{incoming_status}"
    return ""


def preserve_review_state(existing: JobPosting, incoming: JobPosting) -> dict[str, Any]:
    values: dict[str, Any] = {}
    existing_rank = REVIEW_STATUS_RANK.get(existing.review_status, 0)
    incoming_rank = REVIEW_STATUS_RANK.get(incoming.review_status, 0)
    chosen = incoming if incoming_rank > existing_rank else existing
    other = existing if chosen is incoming else incoming
    conflict = existing.manual_decision_conflict or incoming.manual_decision_conflict or _review_conflict(existing, incoming)

    review_fields = [
        "review_status", "reviewer", "interest_decision", "manual_authoritative_url",
        "follow_up_date", "dismissal_reason", "dismissal_detail", "application_url",
        "resume_version", "cover_letter_version", "referral_or_contact",
        "interview_stage", "next_action", "next_action_date",
    ]
    for field_name in review_fields:
        chosen_value = getattr(chosen, field_name)
        other_value = getattr(other, field_name)
        values[field_name] = chosen_value if _has_value(chosen_value) else other_value

    values["reviewed_date"] = _date_max(existing.reviewed_date, incoming.reviewed_date)
    values["review_notes"] = _merge_text(existing.review_notes, incoming.review_notes)
    values["manual_priority"] = max(
        [value for value in [existing.manual_priority, incoming.manual_priority] if value is not None],
        default=None,
    )
    values["manual_fit_rating"] = max(
        [value for value in [existing.manual_fit_rating, incoming.manual_fit_rating] if value is not None],
        default=None,
    )
    existing_app_rank = APPLICATION_STATUS_RANK.get(existing.application_status, 0)
    incoming_app_rank = APPLICATION_STATUS_RANK.get(incoming.application_status, 0)
    app_chosen = incoming if incoming_app_rank > existing_app_rank else existing
    values["application_status"] = app_chosen.application_status or chosen.application_status
    values["application_date"] = _date_min(existing.application_date, incoming.application_date)
    values["last_application_update"] = _date_max(existing.last_application_update, incoming.last_application_update)
    values["manual_decision_conflict"] = conflict
    return values


def merge_review_state(existing: JobPosting, incoming: JobPosting) -> JobPosting:
    merged = _copy_job(existing)
    for field_name, value in preserve_review_state(existing, incoming).items():
        setattr(merged, field_name, value)
    return JobPosting.from_dict(merged.to_dict())


def _visible_score(job: JobPosting) -> int:
    return job.verified_total_score if job.verified_total_score is not None else job.total_score


def _score_band(score: int) -> str:
    if score >= 85:
        return "85_plus"
    if score >= 75:
        return "75_to_84"
    if score >= 60:
        return "60_to_74"
    if score >= 40:
        return "40_to_59"
    return "under_40"


def _company_tier(company: str, target_company_rows: list[dict[str, Any]] | None) -> str:
    normalized = str(company or "").strip().lower()
    for row in target_company_rows or []:
        if str(row.get("company_name", "")).strip().lower() == normalized:
            return str(row.get("priority_tier") or "target_company")
    return "not_target"


@dataclass(slots=True)
class FeedbackMetrics:
    reviewed_jobs: int
    interested_jobs: int
    applied_jobs: int
    dismissed_jobs: int
    total_jobs: int
    dismissal_reasons: dict[str, int]
    reviewed_by_score_band: dict[str, int]
    reviewed_by_role_family: dict[str, int]
    reviewed_by_company_tier: dict[str, int]
    average_manual_fit_minus_score_band: float | None
    false_positives: int
    potential_missed_opportunities: int

    @property
    def review_rate(self) -> float:
        return self.reviewed_jobs / self.total_jobs if self.total_jobs else 0.0

    @property
    def interest_rate(self) -> float:
        return self.interested_jobs / self.reviewed_jobs if self.reviewed_jobs else 0.0

    @property
    def application_rate(self) -> float:
        return self.applied_jobs / self.reviewed_jobs if self.reviewed_jobs else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_jobs": self.total_jobs,
            "reviewed_jobs": self.reviewed_jobs,
            "review_rate": self.review_rate,
            "interested_jobs": self.interested_jobs,
            "interest_rate": self.interest_rate,
            "applied_jobs": self.applied_jobs,
            "application_rate": self.application_rate,
            "dismissed_jobs": self.dismissed_jobs,
            "dismissal_reasons": self.dismissal_reasons,
            "reviewed_by_score_band": self.reviewed_by_score_band,
            "reviewed_by_role_family": self.reviewed_by_role_family,
            "reviewed_by_company_tier": self.reviewed_by_company_tier,
            "average_manual_fit_minus_score_band": self.average_manual_fit_minus_score_band,
            "false_positives": self.false_positives,
            "potential_missed_opportunities": self.potential_missed_opportunities,
        }


def build_feedback_metrics(jobs: list[JobPosting], target_company_rows: list[dict[str, Any]] | None = None) -> FeedbackMetrics:
    reviewed = [job for job in jobs if job.review_status != "not_reviewed" or _has_value(job.reviewed_date)]
    interested_statuses = {"interested", "applied", "interviewing", "offer"}
    applied_statuses = {"applied", "interviewing", "offer", "rejected", "withdrawn", "closed"}
    dismissed_statuses = {"dismissed", "rejected", "withdrawn", "closed"}
    dismissal_reasons = Counter(job.dismissal_reason or "unknown" for job in reviewed if job.review_status in dismissed_statuses)
    score_bands = Counter(_score_band(_visible_score(job)) for job in reviewed)
    role_families = Counter(job.role_family or "Unknown" for job in reviewed)
    company_tiers = Counter(_company_tier(job.company, target_company_rows) for job in reviewed)
    fit_deltas = []
    false_positives = 0
    potential_missed = 0
    for job in reviewed:
        visible_score = _visible_score(job)
        if job.manual_fit_rating is not None:
            fit_deltas.append(job.manual_fit_rating - round(visible_score / 10))
        if visible_score >= 75 and job.review_status == "dismissed":
            false_positives += 1
        if visible_score < 60 and job.review_status in interested_statuses:
            potential_missed += 1
        if visible_score < 60 and job.manual_fit_rating is not None and job.manual_fit_rating >= 8:
            potential_missed += 1
    return FeedbackMetrics(
        total_jobs=len(jobs),
        reviewed_jobs=len(reviewed),
        interested_jobs=sum(1 for job in reviewed if job.review_status in interested_statuses),
        applied_jobs=sum(1 for job in reviewed if job.review_status in applied_statuses or job.application_status in applied_statuses),
        dismissed_jobs=sum(1 for job in reviewed if job.review_status in dismissed_statuses),
        dismissal_reasons=dict(dismissal_reasons),
        reviewed_by_score_band=dict(score_bands),
        reviewed_by_role_family=dict(role_families),
        reviewed_by_company_tier=dict(company_tiers),
        average_manual_fit_minus_score_band=(sum(fit_deltas) / len(fit_deltas)) if fit_deltas else None,
        false_positives=false_positives,
        potential_missed_opportunities=potential_missed,
    )


def build_calibration_report_rows(jobs: list[JobPosting], target_company_rows: list[dict[str, Any]] | None = None) -> list[list[Any]]:
    metrics = build_feedback_metrics(jobs, target_company_rows)
    rows: list[list[Any]] = [
        ["Calibration metric", "Value", "Interpretation"],
        ["Review rate", f"{metrics.review_rate:.1%}", "Share of jobs with any human review state"],
        ["Interest rate", f"{metrics.interest_rate:.1%}", "Share of reviewed jobs marked interested or later"],
        ["Application rate", f"{metrics.application_rate:.1%}", "Share of reviewed jobs that reached application workflow"],
        ["False positives", metrics.false_positives, "High automated score but dismissed by reviewer"],
        ["Potential missed opportunities", metrics.potential_missed_opportunities, "Lower automated score but positive manual signal"],
    ]
    for reason, count in sorted(metrics.dismissal_reasons.items(), key=lambda item: (-item[1], item[0]))[:8]:
        rows.append([f"Dismissal reason: {reason}", count, "Use for future manual scoring calibration only"])
    if metrics.average_manual_fit_minus_score_band is not None:
        rows.append([
            "Average manual fit less score band",
            round(metrics.average_manual_fit_minus_score_band, 2),
            "Positive means manual fit tends to exceed automated score band",
        ])
    rows.append(["Scoring weight changes", "not_applied", "Sprint 36 reports calibration only and does not change production weights"])
    return rows


def _is_excluded_from_review_queue(job: JobPosting) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return (
        job.alert_tier == "exclude"
        or job.score_status == "excluded"
        or job.verified_alert_tier == "exclude"
        or "hard_exclude=true" in explanation
    )


def _is_open_for_review(job: JobPosting) -> bool:
    return job.status in {"open", "reopened", "not_seen_once", "likely_closed"} and not _is_excluded_from_review_queue(job)


def _format_job_row(job: JobPosting) -> list[Any]:
    visible_score = _visible_score(job)
    manual_priority = job.manual_priority if job.manual_priority is not None else ""
    return [
        job.company,
        job.title,
        job.location,
        job.review_status,
        manual_priority,
        job.manual_fit_rating if job.manual_fit_rating is not None else "",
        visible_score,
        job.potential_priority,
        job.follow_up_date or job.next_action_date,
        job.next_action,
        job.application_status,
        job.canonical_url or job.application_url,
        job.review_notes,
    ]


def _queue_rows(title: str, jobs: list[JobPosting], empty_label: str, limit: int = 10) -> list[list[Any]]:
    rows: list[list[Any]] = [[title], ["Company", "Title", "Location", "Review status", "Manual priority", "Manual fit", "Score", "Potential", "Due date", "Next action", "Application", "URL", "Notes"]]
    selected = sorted_for_action_queue(jobs)[:limit]
    rows.extend(_format_job_row(job) for job in selected)
    if not selected:
        rows.append([empty_label, "", "", "", "", "", "", "", "", "", "", "", ""])
    return rows


def build_review_dashboard_sections(jobs: list[JobPosting], *, as_of: str | None = None) -> list[list[Any]]:
    as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso())
    visible_jobs = [job for job in jobs if not _is_excluded_from_review_queue(job)]
    open_jobs = [job for job in visible_jobs if _is_open_for_review(job)]

    def due(value: str) -> bool:
        date_value = parse_iso_date(value)
        return bool(date_value and as_of_date and date_value <= as_of_date)

    review_now = [job for job in open_jobs if job.review_status in {"review_now", "reviewing"} or (job.review_status == "not_reviewed" and job.manual_priority is not None)]
    interested = [job for job in open_jobs if job.review_status == "interested"]
    deferred = [job for job in open_jobs if job.review_status == "deferred" and due(job.follow_up_date)]
    submitted = [job for job in visible_jobs if job.review_status == "applied" or job.application_status == "applied"]
    interviews = [job for job in visible_jobs if job.review_status == "interviewing" or job.application_status == "interviewing"]
    offers = [job for job in visible_jobs if job.review_status == "offer" or job.application_status == "offer"]
    stale_apps = [job for job in submitted if due(job.next_action_date)]
    upcoming = [job for job in visible_jobs if job.next_action and due(job.next_action_date)]

    rows: list[list[Any]] = [
        ["Metric", "Count", "Meaning"],
        ["Review now", len(review_now), "Manual or system-visible roles needing review"],
        ["Interested", len(interested), "Roles kept active after human review"],
        ["Deferred follow-ups", len(deferred), "Deferred roles with due follow-up dates"],
        ["Applications submitted", len(submitted), "Roles with submitted application state"],
        ["Interviews in progress", len(interviews), "Roles currently in interview workflow"],
        ["Offers", len(offers), "Roles with offer status"],
        ["Stale applications needing follow-up", len(stale_apps), "Submitted applications with due next action dates"],
        ["Upcoming next actions", len(upcoming), "Any role with a due next action"],
        [],
    ]
    for title, selected, empty_label in [
        ("Review now queue", review_now, "No jobs need review now"),
        ("Interested queue", interested, "No interested jobs"),
        ("Deferred follow-ups queue", deferred, "No deferred follow-ups due"),
        ("Applications submitted queue", submitted, "No submitted applications"),
        ("Interviews in progress queue", interviews, "No interviews in progress"),
        ("Offers queue", offers, "No offers"),
        ("Stale applications needing follow-up queue", stale_apps, "No stale applications"),
        ("Upcoming next actions queue", upcoming, "No due next actions"),
    ]:
        rows.extend(_queue_rows(title, selected, empty_label))
        rows.append([])
    rows.append(["Feedback calibration"])
    rows.extend(build_calibration_report_rows(jobs))
    return rows
