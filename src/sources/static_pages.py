from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.normalize import normalize_raw_job

LIKELY_JOB_TERMS = ("job", "career", "opening", "position", "role")


def fetch_static_page_jobs(company_row: dict[str, Any], timeout_seconds: int = 20):
    source_url = str(company_row.get("source_url", "")).strip()
    if not source_url:
        return []
    response = requests.get(source_url, timeout=timeout_seconds)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    company_name = company_row.get("company_name", "")
    jobs = []
    seen_urls = set()
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(source_url, anchor["href"])
        combined = f"{text} {href}".lower()
        if not text or not any(term in combined for term in LIKELY_JOB_TERMS) or href in seen_urls:
            continue
        seen_urls.add(href)
        jobs.append(
            normalize_raw_job(
                {
                    "company": company_name,
                    "title": text,
                    "location": company_row.get("location_focus", ""),
                    "url": href,
                    "source_job_id": href,
                    "description": "Low-confidence static page extraction. Review manually.",
                },
                source_primary="static_page",
            )
        )
    return jobs
