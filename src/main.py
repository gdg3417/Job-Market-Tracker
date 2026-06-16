from __future__ import annotations

import argparse
import json
from datetime import datetime

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job
from src.settings import load_settings
from src.sheets import SheetClient, run_sprint2_smoke_test
from src.sources.greenhouse import greenhouse_company_rows, run_greenhouse_companies
from src.sources.lever import lever_company_rows, run_lever_companies


SAMPLE_JOB = {
    "company": "Sample Industrial Co",
    "title": "Senior Manager, Commercial Strategy and Revenue Growth",
    "location": "Plano, TX Hybrid",
    "salary": "$160,000 - $205,000",
    "url": "https://example.com/jobs/123?utm_source=test",
    "source_job_id": "sample-123",
    "description": "Own revenue growth, margin expansion, pricing strategy, operating cadence, and executive leadership reporting for a business unit.",
}


def run_dry_smoke_test() -> dict[str, object]:
    settings = load_settings()
    rules = load_scoring_rules(settings.scoring_rules_path)
    job = normalize_raw_job(SAMPLE_JOB, source_primary="sample")
    scored = score_job(job, rules, company_context={"industry_bucket": "industrial products manufacturing", "ownership_type": "private company"})
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
        "note": "Sprint 5 fetches and scores Greenhouse jobs. Job upsert to the Jobs tab is intentionally left for Sprint 7.",
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
        "note": "Sprint 6 fetches and scores Lever jobs. Job upsert to the Jobs tab is intentionally left for Sprint 7.",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()

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
