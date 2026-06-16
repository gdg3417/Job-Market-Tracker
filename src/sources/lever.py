from __future__ import annotations

from typing import Any

import requests

from src.normalize import normalize_raw_job

LEVER_URL_TEMPLATE = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch_lever_jobs(company_row: dict[str, Any], timeout_seconds: int = 20):
    slug = str(company_row.get("source_slug", "")).strip()
    if not slug:
        return []
    response = requests.get(LEVER_URL_TEMPLATE.format(slug=slug), timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    company_name = company_row.get("company_name", "")
    jobs = []
    for raw_job in payload:
        categories = raw_job.get("categories") or {}
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        description = " ".join(str(raw_job.get(key, "")) for key in ["descriptionPlain", "description", "additionalPlain", "additional"] if raw_job.get(key))
        jobs.append(
            normalize_raw_job(
                {
                    "company": company_name,
                    "title": raw_job.get("text", ""),
                    "location": location,
                    "url": raw_job.get("hostedUrl", ""),
                    "source_job_id": raw_job.get("id", ""),
                    "description": description,
                },
                source_primary="lever",
            )
        )
    return jobs
