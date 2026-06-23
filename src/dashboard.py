from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

OPEN_STATUSES = {"open", "reopened"}
WEEKLY_LOOKBACK_DAYS = 7
SPARSE_GMAIL_REVIEW_REASON = "sparse_gmail_high_signal_title"
ENRICHMENT_FAILURE_STATUSES = {"ambiguous", "not_found", "retryable_failure", "permanent_failure"}
DIGEST_HEADERS = "digest_section company title location remote_status work_model commute_estimate_minutes role_family role_level total_score alert_tier salary_min salary_max total_comp_estimate days_open first_seen_date last_seen_date canonical_url score_explanation potential_priority_score potential_priority evidence_completeness_score score_status verified_total_score verified_alert_tier enrichment_status".split()
PNL_PATH_TERMS = [
    "p&l",
    "profit and loss",
    "general manager",
    "business unit",
    "segment strategy",
    "product line",
    "category management",
    "commercial strategy",
    "revenue strategy",
    "revenue growth",
    "margin expansion",
    "value creation",
]
SOURCE_AUDIT_REJECTION_TERMS = [
    "source",
    "search page",
    "category page",
    "landing page",
    "near-me",
    "job board",
    "navigation",
    "generic",
    "alert metadata",
    "tracking url",
]
TARGET_PRIORITY_TERMS = {"tier 1", "tier 2", "target", "watchlist", "high"}
DIGEST_SECTION_LIMITS = {
    "Immediate review": 10,
    "Verified strong fits": 10,
    "High-potential roles awaiting enrichment": 15,
    "High-potential roles with partial evidence": 15,
    "Enrichment failures requiring review": 15,
    "Strong fit": 10,
    "High-signal titles needing review": 15,
    "Target company watchlist": 10,
    "Needs salary research": 10,
    "Remote or short commute": 10,
    "P&L pathway": 10,
    "New this week": 10,
    "Closed or likely closed this week": 10,
    "Rejected source audit": 5,
}
TOP_ROLE_SECTIONS = [
    "Immediate review",
    "Verified strong fits",
    "High-potential roles awaiting enrichment",
    "High-potential roles with partial evidence",
    "Enrichment failures requiring review",
    "Strong fit",
    "High-signal titles needing review",
    "Target company watchlist",
    "P&L pathway",
    "Needs salary research",
]


@dataclass(slots=True)
class DashboardDigestResult:
    jobs_read: int
    open_jobs: int
    digest_rows: int
    immediate_review_rows: int
    strong_fit_rows: int
    high_signal_review_rows: int
    target_company_watchlist_rows: int
    needs_salary_research_rows: int
    remote_or_short_commute_rows: int
    pnl_pathway_rows: int
    rejected_source_audit_rows: int
    dashboard_rows_written: int
    digest_rows_written: int
    verified_strong_fit_rows: int = 0
    high_potential_pending_rows: int = 0
    high_potential_partial_rows: int = 0
    enrichment_failure_rows: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "jobs_read": self.jobs_read,
            "open_jobs": self.open_jobs,
            "digest_rows": self.digest_rows,
            "immediate_review_rows": self.immediate_review_rows,
            "strong_fit_rows": self.strong_fit_rows,
            "verified_strong_fit_rows": self.verified_strong_fit_rows,
            "high_potential_pending_rows": self.high_potential_pending_rows,
            "high_potential_partial_rows": self.high_potential_partial_rows,
            "enrichment_failure_rows": self.enrichment_failure_rows,
            "high_signal_review_rows": self.high_signal_review_rows,
            "target_company_watchlist_rows": self.target_company_watchlist_rows,
            "needs_salary_research_rows": self.needs_salary_research_rows,
            "remote_or_short_commute_rows": self.remote_or_short_commute_rows,
            "pnl_pathway_rows": self.pnl_pathway_rows,
            "rejected_source_audit_rows": self.rejected_source_audit_rows,
            "dashboard_rows_written": self.dashboard_rows_written,
            "digest_rows_written": self.digest_rows_written,
        }


def _identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _is_truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _row_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _is_open(job: JobPosting) -> bool:
    return job.status in OPEN_STATUSES


def _is_verified(job: JobPosting) -> bool:
    return job.score_status == "verified"


def _verified_score(job: JobPosting) -> int | None:
    if not _is_verified(job):
        return None
    return job.verified_total_score if job.verified_total_score is not None else job.total_score


def _verified_tier(job: JobPosting) -> str:
    if not _is_verified(job):
        return ""
    return job.verified_alert_tier or job.alert_tier


def _has_salary(job: JobPosting) -> bool:
    return job.salary_min is not None or job.salary_max is not None or job.total_comp_estimate is not None


def _is_recent(value: str, *, as_of: str, days: int = WEEKLY_LOOKBACK_DAYS) -> bool:
    row_date = parse_iso_date(value)
    as_of_date = parse_iso_date(as_of)
    if row_date is None or as_of_date is None:
        return False
    delta = (as_of_date - row_date).days
    return 0 <= delta <= days


def _contains_pnl_path_term(job: JobPosting) -> bool:
    text = f"{job.title} {job.role_family} {job.description_text} {job.score_explanation}".lower()
    return any(term in text for term in PNL_PATH_TERMS)


def _is_pnl_pathway_job(job: JobPosting) -> bool:
    return _is_open(job) and (job.p_and_l_path_score >= 14 or _contains_pnl_path_term(job))


def _is_remote_or_short_commute(job: JobPosting) -> bool:
    remote_text = f"{job.remote_status} {job.work_model} {job.location}".lower()
    is_remote = "remote" in remote_text
    is_hybrid = "hybrid" in remote_text
    short_commute = job.commute_estimate_minutes is not None and job.commute_estimate_minutes <= 30
    return _is_open(job) and (is_remote or is_hybrid or short_commute)


def _is_sparse_gmail_review_job(job: JobPosting, *, as_of: str) -> bool:
    explanation = str(job.score_explanation or "").lower()
    return (
        _is_open(job)
        and _is_recent(job.first_seen_date, as_of=as_of)
        and "manual_review=true" in explanation
        and f"review_reason={SPARSE_GMAIL_REVIEW_REASON}" in explanation
        and "hard_exclude=true" not in explanation
        and job.alert_tier != "exclude"
        and job.potential_priority != "high"
    )


def _is_high_potential_pending(job: JobPosting) -> bool:
    return (
        _is_open(job)
        and job.potential_priority == "high"
        and job.score_status == "provisional"
        and job.enrichment_status in {"pending", "in_progress"}
    )


def _is_high_potential_partial(job: JobPosting) -> bool:
    return (
        _is_open(job)
        and job.potential_priority == "high"
        and (job.score_status == "partially_verified" or job.enrichment_status == "partial")
    )


def _is_enrichment_failure(job: JobPosting) -> bool:
    return _is_open(job) and job.enrichment_status in ENRICHMENT_FAILURE_STATUSES


def _target_company_keys(*row_groups: list[dict[str, Any]] | None) -> set[str]:
    keys: set[str] = set()
    for rows in row_groups:
        for row in rows or []:
            if not _is_truthy(row.get("active"), default=True):
                continue
            priority = _identity(row.get("priority_tier"))
            score_boost = row.get("score_boost_points")
            try:
                has_boost = int(float(str(score_boost).strip() or 0)) > 0
            except ValueError:
                has_boost = False
            if priority in TARGET_PRIORITY_TERMS or priority.startswith("tier 1") or priority.startswith("tier 2") or has_boost:
                company_name = _row_value(row, "company_name", "parent_company")
                if company_name:
                    keys.add(_identity(company_name))
    return keys


def _is_target_company_job(job: JobPosting, target_keys: set[str]) -> bool:
    visible_score = _verified_score(job)
    potential_visible = job.potential_priority in {"high", "medium"}
    return (
        bool(target_keys)
        and _is_open(job)
        and _identity(job.company) in target_keys
        and (potential_visible or (visible_score is not None and visible_score >= 50))
    )


def _job_identity(job: JobPosting) -> str:
    return job.job_key or "|".join([_identity(job.company), _identity(job.title), _identity(job.location), job.canonical_url])


def _job_to_digest_row(section: str, job: JobPosting) -> list[Any]:
    verified_score = _verified_score(job)
    verified_tier = _verified_tier(job)
    display_score = verified_score if verified_score is not None else job.total_score
    display_tier = verified_tier or ("pending_verification" if job.score_status != "excluded" else "exclude")
    return [
        section,
        job.company,
        job.title,
        job.location,
        job.remote_status,
        job.work_model,
        job.commute_estimate_minutes if job.commute_estimate_minutes is not None else "",
        job.role_family,
        job.role_level,
        display_score,
        display_tier,
        job.salary_min if job.salary_min is not None else "",
        job.salary_max if job.salary_max is not None else "",
        job.total_comp_estimate if job.total_comp_estimate is not None else "",
        job.days_open,
        job.first_seen_date,
        job.last_seen_date,
        job.canonical_url,
        job.score_explanation,
        job.potential_priority_score,
        job.potential_priority,
        job.evidence_completeness_score,
        job.score_status,
        job.verified_total_score if job.verified_total_score is not None else "",
        job.verified_alert_tier,
        job.enrichment_status,
    ]


def _sort_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    return sorted(
        jobs,
        key=lambda job: (
            _verified_score(job) if _verified_score(job) is not None else -1,
            job.potential_priority_score,
            job.evidence_completeness_score,
            job.p_and_l_path_score,
            job.growth_ownership_score,
            job.last_seen_date,
        ),
        reverse=True,
    )


def _append_job_section(rows: list[list[Any]], seen: set[str], section: str, selected_jobs: list[JobPosting], limit: int) -> None:
    added = 0
    for job in _sort_jobs(selected_jobs):
        identity = _job_identity(job)
        if identity in seen:
            continue
        rows.append(_job_to_digest_row(section, job))
        seen.add(identity)
        added += 1
        if added >= limit:
            break


def _looks_like_source_audit_rejection(row: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in row.values()).lower()
    return any(term in text for term in SOURCE_AUDIT_REJECTION_TERMS)


def _rejected_to_digest_row(row: dict[str, Any]) -> list[Any]:
    reason = _row_value(row, "rejection_reason")
    source = _row_value(row, "source")
    subject = _row_value(row, "subject")
    notes = _row_value(row, "extraction_notes", "raw_evidence")
    explanation = "; ".join(
        part
        for part in [
            f"rejected={reason}" if reason else "rejected=true",
            f"source={source}" if source else "",
            f"subject={subject}" if subject else "",
            notes,
        ]
        if part
    )
    values = [
        "Rejected source audit",
        _row_value(row, "company"),
        _row_value(row, "title"),
        _row_value(row, "location"),
        "",
        "",
        "",
        "",
        "",
        "",
        "rejected",
        "",
        "",
        "",
        "",
        _row_value(row, "received_date"),
        _row_value(row, "created_at", "updated_at"),
        _row_value(row, "url"),
        explanation,
    ]
    return [*values, *[""] * (len(DIGEST_HEADERS) - len(values))]


def build_digest_rows(
    jobs: list[JobPosting],
    *,
    as_of: str | None = None,
    target_company_rows: list[dict[str, Any]] | None = None,
    config_company_rows: list[dict[str, Any]] | None = None,
    rejected_job_rows: list[dict[str, Any]] | None = None,
) -> list[list[Any]]:
    as_of_date = as_of or today_iso()
    target_keys = _target_company_keys(target_company_rows, config_company_rows)
    rows: list[list[Any]] = []
    seen: set[str] = set()
    sections: list[tuple[str, list[JobPosting], int]] = [
        (
            "Immediate review",
            [job for job in jobs if _is_open(job) and _is_verified(job) and (_verified_tier(job) == "immediate_review" or (_verified_score(job) or 0) >= 85)],
            DIGEST_SECTION_LIMITS["Immediate review"],
        ),
        (
            "Verified strong fits",
            [job for job in jobs if _is_open(job) and _is_verified(job) and 75 <= (_verified_score(job) or 0) < 85],
            DIGEST_SECTION_LIMITS["Verified strong fits"],
        ),
        (
            "High-potential roles awaiting enrichment",
            [job for job in jobs if _is_high_potential_pending(job)],
            DIGEST_SECTION_LIMITS["High-potential roles awaiting enrichment"],
        ),
        (
            "High-potential roles with partial evidence",
            [job for job in jobs if _is_high_potential_partial(job)],
            DIGEST_SECTION_LIMITS["High-potential roles with partial evidence"],
        ),
        (
            "Enrichment failures requiring review",
            [job for job in jobs if _is_enrichment_failure(job)],
            DIGEST_SECTION_LIMITS["Enrichment failures requiring review"],
        ),
        (
            "Strong fit",
            [job for job in jobs if _is_open(job) and _is_verified(job) and 75 <= (_verified_score(job) or 0) < 85],
            DIGEST_SECTION_LIMITS["Strong fit"],
        ),
        (
            "High-signal titles needing review",
            [job for job in jobs if _is_sparse_gmail_review_job(job, as_of=as_of_date)],
            DIGEST_SECTION_LIMITS["High-signal titles needing review"],
        ),
        (
            "Target company watchlist",
            [job for job in jobs if _is_target_company_job(job, target_keys)],
            DIGEST_SECTION_LIMITS["Target company watchlist"],
        ),
        (
            "Needs salary research",
            [job for job in jobs if _is_open(job) and not _has_salary(job) and ((_verified_score(job) or 0) >= 60 or job.potential_priority == "high")],
            DIGEST_SECTION_LIMITS["Needs salary research"],
        ),
        (
            "Remote or short commute",
            [job for job in jobs if _is_remote_or_short_commute(job) and ((_verified_score(job) or 0) >= 60 or job.potential_priority == "high")],
            DIGEST_SECTION_LIMITS["Remote or short commute"],
        ),
        (
            "P&L pathway",
            [job for job in jobs if _is_pnl_pathway_job(job) and ((_verified_score(job) or 0) >= 60 or job.potential_priority == "high")],
            DIGEST_SECTION_LIMITS["P&L pathway"],
        ),
        (
            "New this week",
            [job for job in jobs if _is_open(job) and _is_recent(job.first_seen_date, as_of=as_of_date) and ((_verified_score(job) or 0) >= 60 or job.potential_priority == "high")],
            DIGEST_SECTION_LIMITS["New this week"],
        ),
        (
            "Closed or likely closed this week",
            [
                job
                for job in jobs
                if (job.status == "confirmed_closed" and _is_recent(job.closed_date, as_of=as_of_date))
                or (
                    job.status == "likely_closed"
                    and (
                        _is_recent(job.closed_date, as_of=as_of_date)
                        or _is_recent(job.updated_at, as_of=as_of_date)
                        or _is_recent(job.last_seen_date, as_of=as_of_date)
                    )
                )
            ],
            DIGEST_SECTION_LIMITS["Closed or likely closed this week"],
        ),
    ]
    for section, selected_jobs, limit in sections:
        _append_job_section(rows, seen, section, selected_jobs, limit)

    rejected_rows = [row for row in rejected_job_rows or [] if _looks_like_source_audit_rejection(row)]
    for rejected_row in sorted(
        rejected_rows,
        key=lambda row: _row_value(row, "created_at", "updated_at", "received_date", "subject"),
        reverse=True,
    )[: DIGEST_SECTION_LIMITS["Rejected source audit"]]:
        rows.append(_rejected_to_digest_row(rejected_row))
    return rows


def _digest_record(row: list[Any]) -> dict[str, Any]:
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(DIGEST_HEADERS)}


def _count_digest_rows(digest_rows: list[list[Any]], section: str) -> int:
    return sum(1 for row in digest_rows if row and row[0] == section)


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _format_money(value: Any) -> str:
    number = _safe_int(value, default=0)
    return f"${number:,}" if number else ""


def _format_comp_from_digest(row: dict[str, Any]) -> str:
    salary_min = _format_money(row.get("salary_min"))
    salary_max = _format_money(row.get("salary_max"))
    total_comp = _format_money(row.get("total_comp_estimate"))
    if salary_min and salary_max:
        base = f"{salary_min} to {salary_max} base"
    elif salary_min:
        base = f"{salary_min}+ base"
    elif salary_max:
        base = f"Up to {salary_max} base"
    else:
        base = ""
    if total_comp:
        return f"{base}; {total_comp} TC" if base else f"{total_comp} TC"
    return base or "Not listed"


def _rejection_reason_counts(rejected_job_rows: list[dict[str, Any]] | None) -> Counter[str]:
    reasons = [
        _row_value(row, "rejection_reason") or "Unknown"
        for row in rejected_job_rows or []
        if _looks_like_source_audit_rejection(row)
    ]
    return Counter(reasons)


def _top_rejection_reason(rejected_job_rows: list[dict[str, Any]] | None) -> str:
    counts = _rejection_reason_counts(rejected_job_rows)
    if not counts:
        return "None"
    reason, count = counts.most_common(1)[0]
    return f"{reason} ({count})"


def _recommended_source_action(reason: str) -> str:
    normalized = _identity(reason)
    if any(term in normalized for term in ["search page", "category page", "landing page", "generic", "navigation", "job board"]):
        return "Disable the source or replace it with a direct ATS or company posting URL"
    if "alert metadata" in normalized:
        return "Leave rejected unless valid postings are also being blocked"
    if "tracking url" in normalized:
        return "Prefer the canonical posting URL before ingestion"
    return "Review source configuration before loosening quality gates"


def _is_disabled_source(row: dict[str, Any]) -> bool:
    return not _is_truthy(row.get("active"), default=True) or _identity(row.get("ingestion_mode")) == "disabled"


def _is_static_source(row: dict[str, Any]) -> bool:
    source_type = _identity(row.get("source_type"))
    ingestion_mode = _identity(row.get("ingestion_mode"))
    return "static" in source_type or ingestion_mode in {"static direct", "static company", "static pages"}


def _source_health_counts(config_company_rows: list[dict[str, Any]] | None) -> dict[str, int]:
    rows = config_company_rows or []
    static_rows = [row for row in rows if _is_static_source(row)]
    return {
        "static_sources_active": sum(1 for row in static_rows if not _is_disabled_source(row)),
        "static_sources_disabled": sum(1 for row in static_rows if _is_disabled_source(row)),
    }


def _parse_run_notes(row: dict[str, Any]) -> dict[str, Any]:
    notes = _row_value(row, "notes")
    if not notes:
        return {}
    try:
        parsed = json.loads(notes)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_run(rows: list[dict[str, Any]] | None, keyword: str) -> dict[str, Any] | None:
    keyword_identity = _identity(keyword)
    matches = []
    for row in rows or []:
        text = _identity(" ".join([_row_value(row, "run_type"), _row_value(row, "source_type"), _row_value(row, "source_name")]))
        if keyword_identity in text:
            matches.append(row)
    if not matches:
        return None
    return sorted(matches, key=lambda row: _row_value(row, "finished_at", "started_at", "created_at"), reverse=True)[0]


def _run_status(rows: list[dict[str, Any]] | None, keyword: str) -> str:
    row = _latest_run(rows, keyword)
    if row is None:
        return "Not logged"
    status = _row_value(row, "status") or "unknown"
    finished_at = _row_value(row, "finished_at", "started_at")
    return f"{status} ({finished_at})" if finished_at else status


def _run_metric(rows: list[dict[str, Any]] | None, keyword: str, fallback_field: str, note_keys: tuple[str, ...]) -> str:
    row = _latest_run(rows, keyword)
    if row is None:
        return "Not logged"
    notes = _parse_run_notes(row)
    for key in note_keys:
        if key in notes:
            return str(notes[key])
    return str(_safe_int(row.get(fallback_field), default=0))


def _dashboard_answer(counts: dict[str, int]) -> str:
    if counts["immediate"] > 0:
        return "Review verified roles now"
    if counts["verified_strong"] > 0 or counts["strong"] > 0:
        return "Review verified strong fits this week"
    if counts["pending"] > 0:
        return "Enrich high-potential roles"
    if counts["partial"] > 0:
        return "Review partially verified roles"
    if counts["failures"] > 0:
        return "Resolve enrichment failures"
    if counts["review"] > 0:
        return "Review high-signal Gmail roles"
    if counts["target"] > 0:
        return "Review target company roles"
    if counts["rejected"] >= 10:
        return "Source cleanup needed"
    return "No action needed this week"


def _metric_rows(counts: dict[str, int]) -> list[list[Any]]:
    return [
        ["Metric", "Count", "Meaning", "Action"],
        ["Immediate review", counts["immediate"], "Verified best opportunities", "Review same day"],
        ["Verified strong fits", counts["verified_strong"], "Evidence-backed strong roles", "Review weekly"],
        ["High-potential roles awaiting enrichment", counts["pending"], "Promising roles with low evidence", "Enrich or open posting"],
        ["High-potential roles with partial evidence", counts["partial"], "Promising roles with incomplete evidence", "Review recovered evidence"],
        ["Enrichment failures requiring review", counts["failures"], "Automated evidence collection needs attention", "Review URL or matching issue"],
        ["High-signal titles needing review", counts["review"], "Legacy sparse Gmail roles not yet classified high potential", "Open posting and review evidence"],
        ["Target company watchlist", counts["target"], "Companies you care about", "Review weekly"],
        ["Needs salary research", counts["salary"], "Could be good, compensation unknown", "Research compensation"],
        ["P&L pathway", counts["pnl"], "Roles with operating ownership signals", "Review for long-term path fit"],
        ["Remote or short commute", counts["remote"], "Roles that improve flexibility or commute", "Review if role quality is acceptable"],
        ["New this week", counts["new"], "Recently surfaced visible roles", "Scan weekly"],
        ["Recently closed", counts["closed"], "Roles that closed or likely closed this week", "No action unless already in process"],
        ["Rejected source audit", counts["rejected"], "Noise blocked by quality gates", "Clean source only if recurring"],
    ]


def _top_role_rows(digest_rows: list[list[Any]], limit: int = 20) -> list[list[Any]]:
    rows = [["Section", "Company", "Title", "Location", "Verified score", "Potential", "Evidence", "Score status", "Enrichment", "Comp", "URL", "Why it matters"]]
    added = 0
    for row in digest_rows:
        if not row or row[0] not in TOP_ROLE_SECTIONS:
            continue
        record = _digest_record(row)
        rows.append(
            [
                record.get("digest_section", ""),
                record.get("company", ""),
                record.get("title", ""),
                record.get("location", ""),
                record.get("verified_total_score", ""),
                f"{record.get('potential_priority', '')} ({record.get('potential_priority_score', '')})".strip(),
                record.get("evidence_completeness_score", ""),
                record.get("score_status", ""),
                record.get("enrichment_status", ""),
                _format_comp_from_digest(record),
                record.get("canonical_url", ""),
                record.get("score_explanation", ""),
            ]
        )
        added += 1
        if added >= limit:
            break
    if added == 0:
        rows.append(["No roles to review", "", "", "", "", "", "", "", "", "", "", ""])
    return rows


def _source_cleanup_rows(rejected_job_rows: list[dict[str, Any]] | None, limit: int = 15) -> list[list[Any]]:
    rows = [["Source", "Rejected title", "Rejected company", "Reason", "Recommended action"]]
    rejected_rows = [row for row in rejected_job_rows or [] if _looks_like_source_audit_rejection(row)]
    for row in sorted(
        rejected_rows,
        key=lambda item: _row_value(item, "created_at", "updated_at", "received_date", "subject"),
        reverse=True,
    )[:limit]:
        reason = _row_value(row, "rejection_reason") or "Unknown"
        rows.append(
            [
                _row_value(row, "source"),
                _row_value(row, "title", "subject"),
                _row_value(row, "company", "sender"),
                reason,
                _recommended_source_action(reason),
            ]
        )
    if len(rows) == 1:
        rows.append(["No source cleanup rows", "", "", "", ""])
    return rows


def build_dashboard_values(
    jobs: list[JobPosting] | None = None,
    *,
    digest_rows: list[list[Any]] | None = None,
    target_company_rows: list[dict[str, Any]] | None = None,
    config_company_rows: list[dict[str, Any]] | None = None,
    rejected_job_rows: list[dict[str, Any]] | None = None,
    runs_rows: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> list[list[Any]]:
    del target_company_rows
    jobs = jobs or []
    digest_rows = digest_rows or []
    source_counts = _source_health_counts(config_company_rows)
    counts = {
        "immediate": _count_digest_rows(digest_rows, "Immediate review"),
        "verified_strong": _count_digest_rows(digest_rows, "Verified strong fits"),
        "pending": _count_digest_rows(digest_rows, "High-potential roles awaiting enrichment"),
        "partial": _count_digest_rows(digest_rows, "High-potential roles with partial evidence"),
        "failures": _count_digest_rows(digest_rows, "Enrichment failures requiring review"),
        "strong": _count_digest_rows(digest_rows, "Strong fit"),
        "review": _count_digest_rows(digest_rows, "High-signal titles needing review"),
        "target": _count_digest_rows(digest_rows, "Target company watchlist"),
        "salary": _count_digest_rows(digest_rows, "Needs salary research"),
        "remote": _count_digest_rows(digest_rows, "Remote or short commute"),
        "pnl": _count_digest_rows(digest_rows, "P&L pathway"),
        "new": _count_digest_rows(digest_rows, "New this week"),
        "closed": _count_digest_rows(digest_rows, "Closed or likely closed this week"),
        "rejected": _count_digest_rows(digest_rows, "Rejected source audit"),
    }
    return [
        ["Job Market Tracker Dashboard"],
        ["Last refreshed", generated_at or utc_now_iso()],
        ["This week's answer", _dashboard_answer(counts)],
        [],
        ["Action queue"],
        *_metric_rows(counts),
        [],
        ["Tracker health"],
        ["Metric", "Status or count", "Meaning"],
        ["Last workflow validation", _run_status(runs_rows, "workflow validation"), "Most recent schema preflight recorded in Runs"],
        ["Last dashboard refresh", "success", "This tab was generated by python -m src.dashboard"],
        ["Jobs read", len(jobs), "Rows read from Jobs"],
        ["Open verified jobs", sum(1 for job in jobs if _is_open(job) and _is_verified(job)), "Open rows with verified scores"],
        ["Open provisional jobs", sum(1 for job in jobs if _is_open(job) and job.score_status == "provisional"), "Open rows still awaiting sufficient evidence"],
        ["Open partially verified jobs", sum(1 for job in jobs if _is_open(job) and job.score_status == "partially_verified"), "Open rows with incomplete recovered evidence"],
        ["Open jobs", sum(1 for job in jobs if _is_open(job)), "Rows with open or reopened status"],
        ["Digest rows", len(digest_rows), "Rows written below the Digest header"],
        ["Rejected source audit rows", counts["rejected"], "Rejected rows surfaced for source cleanup"],
        [],
        ["Source health"],
        ["Metric", "Value", "Meaning"],
        ["Static sources active", source_counts["static_sources_active"], "Configured static sources still eligible to run"],
        ["Static sources disabled", source_counts["static_sources_disabled"], "Static sources turned off or marked inactive"],
        ["Gmail jobs accepted last run", _run_metric(runs_rows, "gmail", "records_inserted", ("gmail_jobs_accepted", "jobs_accepted", "records_inserted")), "Accepted jobs from the latest Gmail run when logged"],
        ["Gmail rows rejected last run", _run_metric(runs_rows, "gmail", "records_failed", ("gmail_alerts_rejected", "rows_rejected", "records_failed")), "Rejected Gmail rows from the latest Gmail run when logged"],
        ["Top rejection reason", _top_rejection_reason(rejected_job_rows), "Most common source audit rejection reason"],
        [],
        ["Top roles to review"],
        *_top_role_rows(digest_rows),
        [],
        ["Source cleanup queue"],
        *_source_cleanup_rows(rejected_job_rows),
    ]


def build_digest_values(
    jobs: list[JobPosting],
    *,
    as_of: str | None = None,
    target_company_rows: list[dict[str, Any]] | None = None,
    config_company_rows: list[dict[str, Any]] | None = None,
    rejected_job_rows: list[dict[str, Any]] | None = None,
) -> list[list[Any]]:
    generated_at = utc_now_iso()
    rows = build_digest_rows(
        jobs,
        as_of=as_of,
        target_company_rows=target_company_rows,
        config_company_rows=config_company_rows,
        rejected_job_rows=rejected_job_rows,
    )
    return [
        ["Job Market Tracker Weekly Digest"],
        ["Generated at", generated_at],
        ["Review order", "Verified immediate review, verified strong fits, high-potential enrichment pending, partial evidence, enrichment failures, target watchlist, compensation research, commute, P&L pathway, new roles, closed roles, rejected source audit"],
        [],
        DIGEST_HEADERS,
        *rows,
    ]


def write_values(sheet_client: SheetClient, worksheet_name: str, values: list[list[Any]]) -> None:
    worksheet = sheet_client.get_worksheet(worksheet_name)
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {worksheet_name}")
    if not values:
        return
    with_quota_backoff(
        lambda: worksheet.update(range_name="A1", values=values, value_input_option="USER_ENTERED"),
        operation_name=f"write worksheet {worksheet_name}",
    )


def build_dashboard_run_record(result: DashboardDigestResult) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint26_dashboard_digest_{run_timestamp}",
        "run_type": "sprint_26_dashboard_digest",
        "source_type": "google_sheets",
        "source_name": "Dashboard and Digest",
        "status": "success",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": result.jobs_read,
        "records_inserted": result.digest_rows,
        "records_updated": result.dashboard_rows_written + result.digest_rows_written,
        "records_failed": 0,
        "rows_read": result.jobs_read,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(result.to_dict(), sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def build_sprint11_run_record(result: DashboardDigestResult) -> dict[str, Any]:
    return build_dashboard_run_record(result)


def _read_optional_records(sheet_client: SheetClient, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(worksheet_name)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def _count_digest_section(digest_values: list[list[Any]], section: str) -> int:
    return sum(1 for row in digest_values[5:] if row and row[0] == section)


def apply_dashboard_and_digest(sheet_client: SheetClient, *, as_of: str | None = None, append_run: bool = True) -> DashboardDigestResult:
    jobs = [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    target_company_rows = _read_optional_records(sheet_client, "Target_Companies")
    config_company_rows = _read_optional_records(sheet_client, "Config_Companies")
    rejected_job_rows = _read_optional_records(sheet_client, "Rejected_Jobs")
    runs_rows = _read_optional_records(sheet_client, "Runs")
    digest_values = build_digest_values(
        jobs,
        as_of=as_of,
        target_company_rows=target_company_rows,
        config_company_rows=config_company_rows,
        rejected_job_rows=rejected_job_rows,
    )
    digest_rows_only = digest_values[5:]
    dashboard_values = build_dashboard_values(
        jobs,
        digest_rows=digest_rows_only,
        target_company_rows=target_company_rows,
        config_company_rows=config_company_rows,
        rejected_job_rows=rejected_job_rows,
        runs_rows=runs_rows,
    )
    digest_rows = max(0, len(digest_values) - 5)
    write_values(sheet_client, "Dashboard", dashboard_values)
    write_values(sheet_client, "Digest", digest_values)
    result = DashboardDigestResult(
        jobs_read=len(jobs),
        open_jobs=sum(1 for job in jobs if _is_open(job)),
        digest_rows=digest_rows,
        immediate_review_rows=_count_digest_section(digest_values, "Immediate review"),
        strong_fit_rows=_count_digest_section(digest_values, "Strong fit"),
        verified_strong_fit_rows=_count_digest_section(digest_values, "Verified strong fits"),
        high_potential_pending_rows=_count_digest_section(digest_values, "High-potential roles awaiting enrichment"),
        high_potential_partial_rows=_count_digest_section(digest_values, "High-potential roles with partial evidence"),
        enrichment_failure_rows=_count_digest_section(digest_values, "Enrichment failures requiring review"),
        high_signal_review_rows=_count_digest_section(digest_values, "High-signal titles needing review"),
        target_company_watchlist_rows=_count_digest_section(digest_values, "Target company watchlist"),
        needs_salary_research_rows=_count_digest_section(digest_values, "Needs salary research"),
        remote_or_short_commute_rows=_count_digest_section(digest_values, "Remote or short commute"),
        pnl_pathway_rows=_count_digest_section(digest_values, "P&L pathway"),
        rejected_source_audit_rows=_count_digest_section(digest_values, "Rejected source audit"),
        dashboard_rows_written=len(dashboard_values),
        digest_rows_written=len(digest_values),
    )
    if append_run:
        sheet_client.append_run(build_dashboard_run_record(result))
    return result


def run_dashboard_digest_refresh() -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_dashboard_and_digest(sheet_client)
    return {"run_mode": "sprint_26_dashboard_digest", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Job Market Tracker Dashboard and Digest tabs")
    parser.add_argument("--no-run-log", action="store_true", help="Refresh tabs without appending a Runs row")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_dashboard_and_digest(sheet_client, append_run=not args.no_run_log)
    print(json.dumps({"run_mode": "sprint_26_dashboard_digest", "status": "success", **result.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
