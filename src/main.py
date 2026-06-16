from __future__ import annotations

import argparse
import json
from datetime import datetime

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job
from src.settings import load_settings


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job Market Tracker")
    parser.add_argument("--dry-run", action="store_true", help="Run a local smoke test without external services")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.dry_run or settings.dry_run:
        print(json.dumps(run_dry_smoke_test(), indent=2))
        return
    raise NotImplementedError("Sprint 2 will add Google Sheets execution. Run with --dry-run for Sprint 1.")


if __name__ == "__main__":
    main()
