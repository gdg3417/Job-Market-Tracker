from __future__ import annotations

from typing import Any

import requests

from src.normalize import normalize_raw_job

GREENHOUSE_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch_greenhouse_jobs(company_row: dict[str, Any], timeout_seconds: int = 20):
    slug = str(company_row.get("source_slug", "")).strip()
    if not slug:
        return []
    response = requests.get(GREENHOUSE_URL_TEMPLATE.format(slug=slug), timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    company_name = company_row.get("company_name", "")
    jobs = []
    for raw_job in payload.get("jobs", []):
        location = raw_job.get("location") or {}
        jobs.append(
            normalize_raw_job(
                {
                    "company": company_name,
                    "title": raw_job.get("title", ""),
                    "location": location.get("name", "") if isinstance(location, dict) else location,
                    "url": raw_job.get("absolute_url", ""),
                    "source_job_id": raw_job.get("id", ""),
                    "description": raw_job.get("content", ""),
                },
                source_primary="greenhouse",
            )
        )
    return jobs
