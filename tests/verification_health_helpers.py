from __future__ import annotations

import json
from datetime import UTC, datetime

AS_OF = datetime(2026, 6, 26, 18, 0, tzinfo=UTC)


def job(job_key: str, **overrides):
    values = {
        "job_key": job_key,
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX",
        "status": "open",
        "potential_priority": "high",
        "score_status": "provisional",
        "enrichment_status": "pending",
        "first_seen_date": "2026-06-24",
        "created_at": "2026-06-24T12:00:00Z",
        "updated_at": "2026-06-24T12:00:00Z",
        "evidence_completeness_score": 20,
        "canonical_url": "",
        "description_text": "",
        "remote_status": "unknown",
        "work_model": "unknown",
    }
    values.update(overrides)
    return values


def successful_daily_run(**overrides):
    values = {
        "run_id": "daily-20260626",
        "run_type": "sprint_32_enrichment_daily",
        "source_name": "Production enrichment pipeline",
        "status": "success",
        "started_at": "2026-06-26T15:00:00Z",
        "finished_at": "2026-06-26T16:00:00Z",
        "records_inserted": 2,
        "records_failed": 0,
        "notes": json.dumps({
            "direct_link": {"direct_attempts": 2, "retryable_failures": 0, "permanent_failures": 0},
            "company_ats": {"company_ats_attempts": 1, "failures": 0},
            "external_search": {"queries_executed": 0, "search_failures": 0},
        }),
    }
    values.update(overrides)
    return values
