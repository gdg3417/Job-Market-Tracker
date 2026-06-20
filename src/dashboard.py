from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any

from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

OPEN_STATUSES = {"open", "reopened"}
WEEKLY_LOOKBACK_DAYS = 7
DIGEST_HEADERS = "digest_section company title location remote_status work_model commute_estimate_minutes role_family role_level total_score alert_tier salary_min salary_max total_comp_estimate days_open first_seen_date last_seen_date canonical_url score_explanation".split()
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


@dataclass(slots=True)
class DashboardDigestResult:
    jobs_read: int
    open_jobs: int
    digest_rows: int
    immediate_review_rows: int
    strong_fit_rows: int
    target_company_watchlist_rows: int
    needs_salary_research_rows: int
    remote_or_short_commute_rows: int
    pnl_pathway_rows: int
    rejected_source_audit_rows: int
    dashboard_rows_written: int
    digest_rows_written: int

    def to_dict(self) -> dict[str, int]:
        return {
            "jobs_read": self.jobs_read,
            "open_jobs": self.open_jobs,
            "digest_rows": self.digest_rows,
            "immediate_review_rows": self.immediate_review_rows,
            "strong_fit_rows": self.strong_fit_rows,
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
    return bool(target_keys) and _is_open(job) and _identity(job.company) in target_keys and job.total_score >= 50


def _job_identity(job: JobPosting) -> str:
    return job.job_key or "|".join([_identity(job.company), _identity(job.title), _identity(job.location), job.canonical_url])


def _job_to_digest_row(section: str, job: JobPosting) -> list[Any]:
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
        job.total_score,
        job.alert_tier,
        job.salary_min if job.salary_min is not None else "",
        job.salary_max if job.salary_max is not None else "",
        job.total_comp_estimate if job.total_comp_estimate is not None else "",
        job.days_open,
        job.first_seen_date,
        job.last_seen_date,
        job.canonical_url,
        job.score_explanation,
    ]


def _sort_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    return sorted(jobs, key=lambda job: (job.total_score, job.p_and_l_path_score, job.growth_ownership_score, job.executive_exposure_score, job.last_seen_date), reverse=True)


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
    explanation = "; ".join(part for part in [f"rejected={reason}" if reason else "rejected=true", f"source={source}" if source else "", f"subject={subject}" if subject else "", notes] if part)
    return [
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
        ("Immediate review", [job for job in jobs if _is_open(job) and (job.alert_tier == "immediate_review" or job.total_score >= 85)], 20),
        ("Strong fit", [job for job in jobs if _is_open(job) and 75 <= job.total_score < 85], 20),
        ("Target company watchlist", [job for job in jobs if _is_target_company_job(job, target_keys)], 20),
        ("Needs salary research", [job for job in jobs if _is_open(job) and job.total_score >= 60 and not _has_salary(job)], 20),
        ("Remote or short commute", [job for job in jobs if _is_remote_or_short_commute(job) and job.total_score >= 60], 20),
        ("P&L pathway", [job for job in jobs if _is_pnl_pathway_job(job) and job.total_score >= 60], 20),
        ("New this week", [job for job in jobs if _is_open(job) and _is_recent(job.first_seen_date, as_of=as_of_date) and job.total_score >= 60], 30),
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
            20,
        ),
    ]
    for section, selected_jobs, limit in sections:
        _append_job_section(rows, seen, section, selected_jobs, limit)
    rejected_rows = [row for row in rejected_job_rows or [] if _looks_like_source_audit_rejection(row)]
    for rejected_row in sorted(rejected_rows, key=lambda row: _row_value(row, "created_at", "updated_at", "received_date", "subject"), reverse=True)[:25]:
        rows.append(_rejected_to_digest_row(rejected_row))
    return rows


def build_dashboard_values() -> list[list[Any]]:
    return [
        ["Job Market Tracker Dashboard", "", "", "", "", "", "", "", "", "", "", ""],
        ["Last refreshed", "=NOW()", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["Weekly review metrics", "", "Value", "", "Digest view", "", "", "", "", "", "", ""],
        ["New jobs this week", "", '=COUNTIF(Digest!A:A,"New this week")', "", "See Digest tab section: New this week", "", "", "", "", "", "", ""],
        ["Immediate review jobs", "", '=COUNTIF(Digest!A:A,"Immediate review")', "", "See Digest tab section: Immediate review", "", "", "", "", "", "", ""],
        ["Strong fit open jobs", "", '=COUNTIF(Digest!A:A,"Strong fit")', "", "See Digest tab section: Strong fit", "", "", "", "", "", "", ""],
        ["Target company watchlist jobs", "", '=COUNTIF(Digest!A:A,"Target company watchlist")', "", "See Digest tab section: Target company watchlist", "", "", "", "", "", "", ""],
        ["Jobs needing salary research", "", '=COUNTIF(Digest!A:A,"Needs salary research")', "", "See Digest tab section: Needs salary research", "", "", "", "", "", "", ""],
        ["Remote or short commute jobs", "", '=COUNTIF(Digest!A:A,"Remote or short commute")', "", "See Digest tab section: Remote or short commute", "", "", "", "", "", "", ""],
        ["P&L pathway jobs", "", '=COUNTIF(Digest!A:A,"P&L pathway")', "", "See Digest tab section: P&L pathway", "", "", "", "", "", "", ""],
        ["Rejected source audit rows", "", '=COUNTIF(Digest!A:A,"Rejected source audit")', "", "See Digest tab section: Rejected source audit", "", "", "", "", "", "", ""],
        ["Track-only open jobs", "", '=COUNTIFS(Jobs!AG2:AG,"track_only",Jobs!S2:S,"open")+COUNTIFS(Jobs!AG2:AG,"track_only",Jobs!S2:S,"reopened")', "", "Tracked, shown only when it fits a focused Digest section", "", "", "", "", "", "", ""],
        ["Closed jobs this week", "", '=COUNTIF(Digest!A:A,"Closed or likely closed this week")', "", "See Digest tab section: Closed or likely closed this week", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["Jobs by role family", "", "", "Jobs by company", "", "", "Jobs by source", "", "", "Jobs by alert tier", "", ""],
        ['=QUERY(Jobs!V2:V,"select V, count(V) where V is not null group by V order by count(V) desc label V \'role_family\', count(V) \'openings\'",0)', "", "", '=QUERY(Jobs!B2:B,"select B, count(B) where B is not null group by B order by count(B) desc label B \'company\', count(B) \'openings\'",0)', "", "", '=QUERY(Jobs!L2:L,"select L, count(L) where L is not null group by L order by count(L) desc label L \'source\', count(L) \'openings\'",0)', "", "", '=QUERY(Jobs!AG2:AG,"select AG, count(AG) where AG is not null group by AG order by count(AG) desc label AG \'alert_tier\', count(AG) \'openings\'",0)', "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["Salary range by role family", "", "", "", "", "Average days open by role family", "", "", "Companies with repeat postings", "", "", ""],
        ['=QUERY({Jobs!V2:V,Jobs!H2:H,Jobs!I2:I,Jobs!K2:K},"select Col1, min(Col2), max(Col3), avg(Col4) where Col1 is not null group by Col1 label Col1 \'role_family\', min(Col2) \'min_salary\', max(Col3) \'max_salary\', avg(Col4) \'avg_total_comp\'",0)', "", "", "", "", '=QUERY({Jobs!V2:V,Jobs!U2:U},"select Col1, avg(Col2) where Col1 is not null group by Col1 label Col1 \'role_family\', avg(Col2) \'avg_days_open\'",0)', "", "", '=QUERY(Jobs!B2:C,"select B, count(C) where B is not null group by B having count(C) > 1 order by count(C) desc label B \'company\', count(C) \'postings\'",0)', "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["Filtered job views", "Digest tab contains focused sections for immediate review, strong fit, target company watchlist, salary research, remote or short commute, P&L pathway, new jobs, recently closed jobs, and rejected source audit rows.", "", "", "", "", "", "", "", "", "", ""],
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
    rows = build_digest_rows(jobs, as_of=as_of, target_company_rows=target_company_rows, config_company_rows=config_company_rows, rejected_job_rows=rejected_job_rows)
    return [
        ["Job Market Tracker Weekly Digest"],
        ["Generated at", generated_at],
        ["Review order", "Immediate review, strong fit, target company watchlist, needs salary research, remote or short commute, P&L pathway, new this week, closed this week, rejected source audit"],
        [],
        DIGEST_HEADERS,
        *rows,
    ]


def write_values(sheet_client: SheetClient, worksheet_name: str, values: list[list[Any]]) -> None:
    worksheet = sheet_client.get_worksheet(worksheet_name)
    with_quota_backoff(lambda: worksheet.clear(), operation_name=f"clear worksheet {worksheet_name}")
    if not values:
        return
    with_quota_backoff(lambda: worksheet.update(range_name="A1", values=values, value_input_option="USER_ENTERED"), operation_name=f"write worksheet {worksheet_name}")


def build_sprint11_run_record(result: DashboardDigestResult) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {"run_id": f"sprint11_dashboard_digest_{run_timestamp}", "run_type": "sprint_11_dashboard_digest", "source_type": "google_sheets", "source_name": "Dashboard and Digest", "status": "success", "started_at": now, "finished_at": now, "duration_seconds": 0, "records_found": result.jobs_read, "records_inserted": result.digest_rows, "records_updated": result.dashboard_rows_written + result.digest_rows_written, "records_failed": 0, "rows_read": result.jobs_read, "config_companies_rows": 0, "config_searches_rows": 0, "companies_read": 0, "searches_read": 0, "error_message": "", "notes": json.dumps(result.to_dict(), sort_keys=True), "created_at": now, "updated_at": now}


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
    dashboard_values = build_dashboard_values()
    digest_values = build_digest_values(jobs, as_of=as_of, target_company_rows=target_company_rows, config_company_rows=config_company_rows, rejected_job_rows=rejected_job_rows)
    digest_rows = max(0, len(digest_values) - 5)
    write_values(sheet_client, "Dashboard", dashboard_values)
    write_values(sheet_client, "Digest", digest_values)
    result = DashboardDigestResult(
        jobs_read=len(jobs),
        open_jobs=sum(1 for job in jobs if _is_open(job)),
        digest_rows=digest_rows,
        immediate_review_rows=_count_digest_section(digest_values, "Immediate review"),
        strong_fit_rows=_count_digest_section(digest_values, "Strong fit"),
        target_company_watchlist_rows=_count_digest_section(digest_values, "Target company watchlist"),
        needs_salary_research_rows=_count_digest_section(digest_values, "Needs salary research"),
        remote_or_short_commute_rows=_count_digest_section(digest_values, "Remote or short commute"),
        pnl_pathway_rows=_count_digest_section(digest_values, "P&L pathway"),
        rejected_source_audit_rows=_count_digest_section(digest_values, "Rejected source audit"),
        dashboard_rows_written=len(dashboard_values),
        digest_rows_written=len(digest_values),
    )
    if append_run:
        sheet_client.append_run(build_sprint11_run_record(result))
    return result


def run_dashboard_digest_refresh() -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_dashboard_and_digest(sheet_client)
    return {"run_mode": "sprint_11_dashboard_digest", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Job Market Tracker Dashboard and Digest tabs")
    parser.add_argument("--no-run-log", action="store_true", help="Refresh tabs without appending a Runs row")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    result = apply_dashboard_and_digest(sheet_client, append_run=not args.no_run_log)
    print(json.dumps({"run_mode": "sprint_11_dashboard_digest", "status": "success", **result.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
