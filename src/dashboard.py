from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff

OPEN_STATUSES = {"open", "reopened"}
WEEKLY_LOOKBACK_DAYS = 7

DIGEST_HEADERS = [
    "digest_section",
    "company",
    "title",
    "location",
    "remote_status",
    "work_model",
    "commute_estimate_minutes",
    "role_family",
    "role_level",
    "total_score",
    "alert_tier",
    "salary_min",
    "salary_max",
    "total_comp_estimate",
    "days_open",
    "first_seen_date",
    "last_seen_date",
    "canonical_url",
    "score_explanation",
]

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


@dataclass(slots=True)
class DashboardDigestResult:
    jobs_read: int
    open_jobs: int
    digest_rows: int
    immediate_review_rows: int
    strong_fit_rows: int
    pnl_pathway_rows: int
    remote_or_short_commute_rows: int
    dashboard_rows_written: int
    digest_rows_written: int

    def to_dict(self) -> dict[str, int]:
        return {
            "jobs_read": self.jobs_read,
            "open_jobs": self.open_jobs,
            "digest_rows": self.digest_rows,
            "immediate_review_rows": self.immediate_review_rows,
            "strong_fit_rows": self.strong_fit_rows,
            "pnl_pathway_rows": self.pnl_pathway_rows,
            "remote_or_short_commute_rows": self.remote_or_short_commute_rows,
            "dashboard_rows_written": self.dashboard_rows_written,
            "digest_rows_written": self.digest_rows_written,
        }


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
    return sorted(
        jobs,
        key=lambda job: (
            job.total_score,
            job.p_and_l_path_score,
            job.growth_ownership_score,
            job.executive_exposure_score,
        ),
        reverse=True,
    )


def build_digest_rows(jobs: list[JobPosting], *, as_of: str | None = None) -> list[list[Any]]:
    as_of_date = as_of or today_iso()
    sections: list[tuple[str, list[JobPosting], int]] = [
        ("Immediate review", [job for job in jobs if _is_open(job) and (job.alert_tier == "immediate_review" or job.total_score >= 85)], 25),
        ("Strong fit", [job for job in jobs if _is_open(job) and 75 <= job.total_score < 85], 25),
        ("P&L pathway", [job for job in jobs if _is_pnl_pathway_job(job)], 25),
        ("Remote, hybrid, or short commute", [job for job in jobs if _is_remote_or_short_commute(job) and job.total_score >= 65], 25),
        ("New this week", [job for job in jobs if _is_open(job) and _is_recent(job.first_seen_date, as_of=as_of_date)], 50),
        (
            "Closed or likely closed this week",
            [
                job
                for job in jobs
                if job.status in {"likely_closed", "confirmed_closed"}
                and (
                    _is_recent(job.closed_date, as_of=as_of_date)
                    or _is_recent(job.updated_at, as_of=as_of_date)
                    or _is_recent(job.last_seen_date, as_of=as_of_date)
                )
            ],
            25,
        ),
        ("Missing salary review", [job for job in jobs if _is_open(job) and job.total_score >= 65 and not _has_salary(job)], 25),
    ]

    rows: list[list[Any]] = []
    for section, selected_jobs, limit in sections:
        for job in _sort_jobs(selected_jobs)[:limit]:
            rows.append(_job_to_digest_row(section, job))
    return rows


def build_dashboard_values() -> list[list[Any]]:
    return [
        ["Job Market Tracker Dashboard", "", "", "", "", "", "", "", "", "", "", ""],
        ["Last refreshed", "=NOW()", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["Weekly review metrics", "", "Value", "", "Digest view", "", "", "", "", "", "", ""],
        ["New jobs this week", "", '=COUNTIFS(Jobs!P2:P,">="&TODAY()-7,Jobs!S2:S,"open")+COUNTIFS(Jobs!P2:P,">="&TODAY()-7,Jobs!S2:S,"reopened")', "", "See Digest tab section: New this week", "", "", "", "", "", "", ""],
        ["Immediate review jobs", "", '=COUNTIFS(Jobs!AG2:AG,"immediate_review",Jobs!S2:S,"open")+COUNTIFS(Jobs!AG2:AG,"immediate_review",Jobs!S2:S,"reopened")', "", "See Digest tab section: Immediate review", "", "", "", "", "", "", ""],
        ["Strong fit open jobs", "", '=COUNTIFS(Jobs!AG2:AG,"strong_fit",Jobs!S2:S,"open")+COUNTIFS(Jobs!AG2:AG,"strong_fit",Jobs!S2:S,"reopened")', "", "See Digest tab section: Strong fit", "", "", "", "", "", "", ""],
        ["Track-only open jobs", "", '=COUNTIFS(Jobs!AG2:AG,"track_only",Jobs!S2:S,"open")+COUNTIFS(Jobs!AG2:AG,"track_only",Jobs!S2:S,"reopened")', "", "Tracked, not included unless also new or missing salary", "", "", "", "", "", "", ""],
        ["P&L pathway jobs", "", '=COUNTIFS(Jobs!Y2:Y,">=14",Jobs!S2:S,"open")+COUNTIFS(Jobs!Y2:Y,">=14",Jobs!S2:S,"reopened")', "", "See Digest tab section: P&L pathway", "", "", "", "", "", "", ""],
        ["Remote jobs", "", '=COUNTIFS(Jobs!F2:F,"*remote*",Jobs!S2:S,"open")+COUNTIFS(Jobs!F2:F,"*remote*",Jobs!S2:S,"reopened")+COUNTIFS(Jobs!E2:E,"*remote*",Jobs!S2:S,"open")+COUNTIFS(Jobs!E2:E,"*remote*",Jobs!S2:S,"reopened")', "", "See Digest tab section: Remote, hybrid, or short commute", "", "", "", "", "", "", ""],
        ["Jobs within 15 minutes", "", '=COUNTIFS(Jobs!G2:G,"<=15",Jobs!S2:S,"open")+COUNTIFS(Jobs!G2:G,"<=15",Jobs!S2:S,"reopened")', "", "Short commute subset", "", "", "", "", "", "", ""],
        ["Jobs within 30 minutes", "", '=COUNTIFS(Jobs!G2:G,"<=30",Jobs!S2:S,"open")+COUNTIFS(Jobs!G2:G,"<=30",Jobs!S2:S,"reopened")', "", "Short commute subset", "", "", "", "", "", "", ""],
        ["Closed jobs this week", "", '=COUNTIFS(Jobs!T2:T,">="&TODAY()-7,Jobs!S2:S,"confirmed_closed")+COUNTIFS(Jobs!S2:S,"likely_closed",Jobs!Q2:Q,">="&TODAY()-7)', "", "See Digest tab section: Closed or likely closed this week", "", "", "", "", "", "", ""],
        ["Jobs with missing salary", "", '=COUNTIFS(Jobs!H2:H,"",Jobs!I2:I,"",Jobs!K2:K,"",Jobs!S2:S,"open")+COUNTIFS(Jobs!H2:H,"",Jobs!I2:I,"",Jobs!K2:K,"",Jobs!S2:S,"reopened")', "", "See Digest tab section: Missing salary review", "", "", "", "", "", "", ""],
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
        ["Filtered job views", "Digest tab contains generated sections for immediate review, strong fit, P&L pathway, remote or short commute, new this week, recently closed, and missing salary.", "", "", "", "", "", "", "", "", "", ""],
    ]


def build_digest_values(jobs: list[JobPosting], *, as_of: str | None = None) -> list[list[Any]]:
    generated_at = utc_now_iso()
    rows = build_digest_rows(jobs, as_of=as_of)
    return [
        ["Job Market Tracker Weekly Digest"],
        ["Generated at", generated_at],
        ["Review order", "Immediate review, strong fit, P&L pathway, remote or short commute, new this week, closed this week, missing salary"],
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


def build_sprint11_run_record(result: DashboardDigestResult) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint11_dashboard_digest_{run_timestamp}",
        "run_type": "sprint_11_dashboard_digest",
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


def apply_dashboard_and_digest(sheet_client: SheetClient, *, as_of: str | None = None, append_run: bool = True) -> DashboardDigestResult:
    jobs = [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    dashboard_values = build_dashboard_values()
    digest_values = build_digest_values(jobs, as_of=as_of)
    digest_rows = max(0, len(digest_values) - 5)

    write_values(sheet_client, "Dashboard", dashboard_values)
    write_values(sheet_client, "Digest", digest_values)

    result = DashboardDigestResult(
        jobs_read=len(jobs),
        open_jobs=sum(1 for job in jobs if _is_open(job)),
        digest_rows=digest_rows,
        immediate_review_rows=sum(1 for row in digest_values[5:] if row and row[0] == "Immediate review"),
        strong_fit_rows=sum(1 for row in digest_values[5:] if row and row[0] == "Strong fit"),
        pnl_pathway_rows=sum(1 for row in digest_values[5:] if row and row[0] == "P&L pathway"),
        remote_or_short_commute_rows=sum(1 for row in digest_values[5:] if row and row[0] == "Remote, hybrid, or short commute"),
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
