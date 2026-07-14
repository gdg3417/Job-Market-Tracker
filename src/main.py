from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from src.gmail_ingestion import run_gmail_ingestion
from src.job_upsert import upsert_jobs
from src.lifecycle import LifecycleSummary, check_job_url_closed, update_lifecycle_for_missing_jobs
from src.models import today_iso, utc_now_iso
from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job
from src.settings import load_settings
from src.sheets import SheetClient, run_sprint2_smoke_test
from src.source_quality import filter_static_sources_for_execution
from src.sources.greenhouse import greenhouse_company_rows, run_greenhouse_companies
from src.sources.lever import lever_company_rows, run_lever_companies
from src.sources.static_pages import run_static_page_companies, static_page_company_rows


SAMPLE_JOB = {
    "company": "Sample Industrial Co",
    "title": "Senior Manager, Commercial Strategy and Revenue Growth",
    "location": "Plano, TX Hybrid",
    "salary": "$160,000 - $205,000",
    "url": "https://example.com/jobs/123?utm_source=test",
    "source_job_id": "sample-123",
    "description": "Own revenue growth, margin expansion, pricing strategy, operating cadence, and executive leadership reporting for a business unit.",
}


def _read_optional_records(sheet_client: Any, worksheet_name: str) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(worksheet_name)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def run_dry_smoke_test() -> dict[str, object]:
    settings = load_settings()
    rules = load_scoring_rules(settings.scoring_rules_path)
    job = normalize_raw_job(SAMPLE_JOB, source_primary="sample")
    scored = score_job(
        job,
        rules,
        company_context={
            "industry_bucket": "industrial products manufacturing",
            "ownership_type": "private company",
        },
    )
    return {
        "run_mode": "dry_run",
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "sample_job_key": scored.job_key,
        "sample_total_score": scored.total_score,
        "sample_alert_tier": scored.alert_tier,
        "sample_role_family": scored.role_family,
        "sample_role_level": scored.role_level,
        "credentials_loaded": bool(settings.google_application_credentials),
        "sheet_id_loaded": bool(settings.google_sheet_id),
    }


def run_greenhouse_smoke_test() -> dict[str, object]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    company_rows = sheet_client.read_records("Config_Companies")
    greenhouse_rows = greenhouse_company_rows(company_rows)
    rules = load_scoring_rules(settings.scoring_rules_path)
    jobs, results = run_greenhouse_companies(
        greenhouse_rows,
        scoring_rules=rules,
        sheet_client=sheet_client,
    )
    failures = [result for result in results if result.status == "failed"]
    if not results:
        status = "no_greenhouse_sources"
    elif failures:
        status = "partial_failure"
    else:
        status = "success"
    return {
        "run_mode": "sprint_5_greenhouse_smoke_test",
        "status": status,
        "config_companies_rows": len(company_rows),
        "greenhouse_sources": len(results),
        "greenhouse_failures": len(failures),
        "jobs_found": len(jobs),
        "runs_rows_appended": len(results),
        "source_results": [result.to_summary() for result in results],
        "top_jobs": [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "total_score": job.total_score,
                "alert_tier": job.alert_tier,
                "canonical_url": job.canonical_url,
            }
            for job in sorted(jobs, key=lambda item: item.total_score, reverse=True)[:10]
        ],
        "note": "Sprint 5 fetches and scores Greenhouse jobs. Job upsert to the Jobs tab is handled by Sprint 7.",
    }


def run_lever_smoke_test() -> dict[str, object]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    company_rows = sheet_client.read_records("Config_Companies")
    lever_rows = lever_company_rows(company_rows)
    rules = load_scoring_rules(settings.scoring_rules_path)
    jobs, results = run_lever_companies(
        lever_rows,
        scoring_rules=rules,
        sheet_client=sheet_client,
    )
    failures = [result for result in results if result.status == "failed"]
    if not results:
        status = "no_lever_sources"
    elif failures:
        status = "partial_failure"
    else:
        status = "success"
    return {
        "run_mode": "sprint_6_lever_smoke_test",
        "status": status,
        "config_companies_rows": len(company_rows),
        "lever_sources": len(results),
        "lever_failures": len(failures),
        "jobs_found": len(jobs),
        "runs_rows_appended": len(results),
        "source_results": [result.to_summary() for result in results],
        "top_jobs": [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "total_score": job.total_score,
                "alert_tier": job.alert_tier,
                "canonical_url": job.canonical_url,
            }
            for job in sorted(jobs, key=lambda item: item.total_score, reverse=True)[:10]
        ],
        "note": "Sprint 6 fetches and scores Lever jobs. Job upsert to the Jobs tab is handled by Sprint 7.",
    }


def build_sprint7_run_record(
    *,
    jobs_found: int,
    source_count: int,
    source_failures: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    if source_count == 0:
        status = "no_sources"
    elif source_failures:
        status = "partial_failure"
    else:
        status = "success"
    return {
        "run_id": f"sprint7_job_upsert_{run_timestamp}",
        "run_type": "sprint_7_job_upsert_smoke_test",
        "source_type": "combined_sources",
        "source_name": "Greenhouse and Lever",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": jobs_found,
        "records_inserted": summary.get("jobs_created", 0),
        "records_updated": summary.get("jobs_updated", 0),
        "records_failed": source_failures,
        "rows_read": source_count,
        "config_companies_rows": source_count,
        "config_searches_rows": 0,
        "companies_read": source_count,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def build_sprint8_run_record(
    *,
    jobs_found: int,
    source_count: int,
    source_failures: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    url_check_failures = int(summary.get("url_checks_failed", 0) or 0)
    if source_count == 0:
        status = "no_sources"
    elif source_failures or url_check_failures:
        status = "partial_failure"
    else:
        status = "success"
    return {
        "run_id": f"sprint8_lifecycle_{run_timestamp}",
        "run_type": "sprint_8_lifecycle_tracking",
        "source_type": "combined_sources",
        "source_name": "Greenhouse and Lever lifecycle pass",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": jobs_found,
        "records_inserted": 0,
        "records_updated": summary.get("rows_updated", 0),
        "records_failed": source_failures + url_check_failures,
        "rows_read": summary.get("records_checked", 0),
        "config_companies_rows": source_count,
        "config_searches_rows": 0,
        "companies_read": source_count,
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def build_sprint10_run_record(
    *,
    jobs_found: int,
    source_count: int,
    source_failures: int,
    search_count: int,
    low_confidence_count: int,
    summary: dict[str, Any],
    skipped_sources: int = 0,
) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    if source_count == 0 and skipped_sources:
        status = "all_sources_in_cooldown"
    elif source_count == 0:
        status = "no_static_page_sources"
    elif source_failures:
        status = "partial_failure"
    elif jobs_found == 0:
        status = "no_jobs_found"
    else:
        status = "success"
    notes = {
        "upsert_summary": summary,
        "low_confidence_count": low_confidence_count,
        "runtime_policy_skips": skipped_sources,
    }
    return {
        "run_id": f"sprint10_static_pages_{run_timestamp}",
        "run_type": "sprint_10_static_page_smoke_test",
        "source_type": "static_page",
        "source_name": "Static company career pages",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": jobs_found,
        "records_inserted": summary.get("jobs_created", 0),
        "records_updated": summary.get("jobs_updated", 0),
        "records_failed": source_failures,
        "rows_read": source_count + search_count,
        "config_companies_rows": source_count + skipped_sources,
        "config_searches_rows": search_count,
        "companies_read": source_count,
        "searches_read": search_count,
        "error_message": "",
        "notes": json.dumps(notes, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def run_job_upsert_smoke_test() -> dict[str, object]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    company_rows = sheet_client.read_records("Config_Companies")
    rules = load_scoring_rules(settings.scoring_rules_path)
    seen_date = today_iso()
    greenhouse_rows = greenhouse_company_rows(company_rows)
    greenhouse_jobs, greenhouse_results = run_greenhouse_companies(
        greenhouse_rows,
        scoring_rules=rules,
        sheet_client=None,
        seen_date=seen_date,
    )
    lever_rows = lever_company_rows(company_rows)
    lever_jobs, lever_results = run_lever_companies(
        lever_rows,
        scoring_rules=rules,
        sheet_client=None,
        seen_date=seen_date,
    )
    all_jobs = greenhouse_jobs + lever_jobs
    source_results = greenhouse_results + lever_results
    source_failures = [result for result in source_results if result.status == "failed"]
    upsert_summary = upsert_jobs(sheet_client, all_jobs, seen_date=seen_date)
    if source_results and not source_failures:
        lifecycle_summary = update_lifecycle_for_missing_jobs(
            sheet_client,
            run_date=seen_date,
            url_checker=check_job_url_closed,
        )
    else:
        lifecycle_summary = LifecycleSummary()
    for result in source_results:
        sheet_client.append_run(result.to_run_record())
    sheet_client.append_run(
        build_sprint7_run_record(
            jobs_found=len(all_jobs),
            source_count=len(source_results),
            source_failures=len(source_failures),
            summary=upsert_summary.to_dict(),
        )
    )
    sheet_client.append_run(
        build_sprint8_run_record(
            jobs_found=len(all_jobs),
            source_count=len(source_results),
            source_failures=len(source_failures),
            summary=lifecycle_summary.to_dict(),
        )
    )
    if not source_results:
        status = "no_sources"
    elif source_failures:
        status = "partial_failure"
    else:
        status = "success"
    return {
        "run_mode": "sprint_7_job_upsert_and_sprint_8_lifecycle_smoke_test",
        "status": status,
        "config_companies_rows": len(company_rows),
        "greenhouse_sources": len(greenhouse_results),
        "lever_sources": len(lever_results),
        "source_failures": len(source_failures),
        "jobs_found": len(all_jobs),
        "upsert_summary": upsert_summary.to_dict(),
        "lifecycle_summary": lifecycle_summary.to_dict(),
        "runs_rows_appended": len(source_results) + 2,
        "top_jobs": [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "total_score": job.total_score,
                "alert_tier": job.alert_tier,
                "canonical_url": job.canonical_url,
            }
            for job in sorted(all_jobs, key=lambda item: item.total_score, reverse=True)[:10]
        ],
    }


def run_gmail_alerts_smoke_test() -> dict[str, Any]:
    """Backward-compatible alias for the Sprint 23 ledger-backed Gmail runner."""
    return run_gmail_ingestion()


def run_static_pages_smoke_test() -> dict[str, object]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    company_rows = sheet_client.read_records("Config_Companies")
    search_rows = sheet_client.read_records("Config_Searches")
    audit_rows = _read_optional_records(sheet_client, "Source_Audit")
    configured_static_rows = static_page_company_rows(company_rows)
    static_rows, skipped_sources = filter_static_sources_for_execution(company_rows, audit_rows)
    rules = load_scoring_rules(settings.scoring_rules_path)
    seen_date = today_iso()
    jobs, results = run_static_page_companies(
        static_rows,
        scoring_rules=rules,
        search_rows=search_rows,
        sheet_client=None,
        seen_date=seen_date,
    )
    source_failures = [result for result in results if result.status == "failed"]
    low_confidence_count = sum(result.low_confidence_count for result in results)
    upsert_summary = upsert_jobs(sheet_client, jobs, seen_date=seen_date)
    for result in results:
        sheet_client.append_run(result.to_run_record())
    sheet_client.append_run(
        build_sprint10_run_record(
            jobs_found=len(jobs),
            source_count=len(results),
            source_failures=len(source_failures),
            search_count=len(search_rows),
            low_confidence_count=low_confidence_count,
            summary=upsert_summary.to_dict(),
            skipped_sources=len(skipped_sources),
        )
    )
    if not results and skipped_sources:
        status = "all_sources_in_cooldown"
    elif not results:
        status = "no_static_page_sources"
    elif source_failures:
        status = "partial_failure"
    elif not jobs:
        status = "no_jobs_found"
    else:
        status = "success"
    return {
        "run_mode": "sprint_10_static_page_support",
        "status": status,
        "config_companies_rows": len(company_rows),
        "config_searches_rows": len(search_rows),
        "static_page_sources_configured": len(configured_static_rows),
        "static_page_sources": len(results),
        "static_page_sources_skipped": len(skipped_sources),
        "static_page_failures": len(source_failures),
        "jobs_found": len(jobs),
        "low_confidence_jobs": low_confidence_count,
        "upsert_summary": upsert_summary.to_dict(),
        "runs_rows_appended": len(results) + 1,
        "source_policy_skips": skipped_sources,
        "source_results": [result.to_summary() for result in results],
        "top_jobs": [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "total_score": job.total_score,
                "alert_tier": job.alert_tier,
                "canonical_url": job.canonical_url,
                "manual_review": "manual_review=true" in job.score_explanation,
            }
            for job in sorted(jobs, key=lambda item: item.total_score, reverse=True)[:10]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job Market Tracker")
    parser.add_argument("--dry-run", action="store_true", help="Run a local smoke test without external services")
    parser.add_argument(
        "--sheets-smoke-test",
        action="store_true",
        help="Read Config_Companies and Config_Searches, then append a Sprint 2 test row to Runs",
    )
    parser.add_argument(
        "--greenhouse-smoke-test",
        action="store_true",
        help="Read active Greenhouse rows, fetch and score jobs, then append Sprint 5 source run rows to Runs",
    )
    parser.add_argument(
        "--lever-smoke-test",
        action="store_true",
        help="Read active Lever rows, fetch and score jobs, then append Sprint 6 source run rows to Runs",
    )
    parser.add_argument(
        "--job-upsert-smoke-test",
        action="store_true",
        help="Fetch Greenhouse and Lever jobs, upsert Jobs, upsert Job_Sources, update lifecycle statuses, then append run rows",
    )
    parser.add_argument(
        "--gmail-alerts-smoke-test",
        action="store_true",
        help="Run the Sprint 23 ledger-backed Gmail ingestion command",
    )
    parser.add_argument(
        "--static-pages-smoke-test",
        action="store_true",
        help="Fetch configured static career pages, extract likely job links, upsert Jobs and Job_Sources, then append Sprint 10 run rows",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.static_pages_smoke_test:
        print(json.dumps(run_static_pages_smoke_test(), indent=2))
        return
    if args.gmail_alerts_smoke_test:
        print(json.dumps(run_gmail_alerts_smoke_test(), indent=2))
        return
    if args.job_upsert_smoke_test:
        print(json.dumps(run_job_upsert_smoke_test(), indent=2))
        return
    if args.lever_smoke_test:
        print(json.dumps(run_lever_smoke_test(), indent=2))
        return
    if args.greenhouse_smoke_test:
        print(json.dumps(run_greenhouse_smoke_test(), indent=2))
        return
    if args.sheets_smoke_test:
        print(json.dumps(run_sprint2_smoke_test(settings), indent=2))
        return
    if args.dry_run or settings.dry_run:
        print(json.dumps(run_dry_smoke_test(), indent=2))
        return
    print(json.dumps(run_sprint2_smoke_test(settings), indent=2))


if __name__ == "__main__":
    main()
