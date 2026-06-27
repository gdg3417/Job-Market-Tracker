from __future__ import annotations

from src.models import JobPosting
from src.resolution.models import PostingResolution
from src.source_reliability_resolution import refresh_source_health_from_resolutions

NOW = "2026-06-27T15:30:00Z"


class FakeSheetClient:
    def __init__(self, tables):
        self.tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables.get(worksheet_name, [])]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables.get(worksheet_name, []))]

    def read_jobs_with_row_numbers(self):
        return [(index + 2, JobPosting.from_dict(row)) for index, row in enumerate(self.tables.get("Jobs", []))]

    def append_record(self, worksheet_name, record):
        self.tables.setdefault(worksheet_name, []).append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables.setdefault(worksheet_name, [])
        self.tables[worksheet_name][row_number - 2] = dict(record)


def job(job_key="job-topgolf", company="Topgolf"):
    return JobPosting(
        job_key=job_key,
        company=company,
        title="Sr Manager, Strategic Planning",
        location="Dallas, TX",
        status="open",
        potential_priority="high",
    ).to_dict()


def config(company="Topgolf", platform="phenom"):
    return {
        "company_id": company.lower().replace(" ", "-"),
        "company_name": company,
        "canonical_company_name": company,
        "career_domain": "careers.topgolf.com",
        "career_search_url": "https://careers.topgolf.com/us/search-results",
        "ats_platform": platform,
        "enrichment_active": True,
    }


def resolution(state="unsupported", job_key="job-topgolf"):
    return PostingResolution(
        resolution_id=f"res-{job_key}",
        job_key=job_key,
        resolution_state=state,
        attempted_at=NOW,
        blocker_reason="no_supported_enrichment_path" if state == "unsupported" else "",
        error_message="No stable configured API adapter is available" if state == "unsupported" else "",
        candidate_count=0,
        created_at=NOW,
        updated_at=NOW,
    ).to_dict()


def test_resolution_attempts_populate_source_health_rows_for_configured_sources():
    client = FakeSheetClient(
        {
            "Jobs": [job()],
            "Config_Companies": [config()],
            "Posting_Resolution": [resolution("unsupported")],
            "Source_Health": [],
        }
    )

    summary = refresh_source_health_from_resolutions(client, observed_at=NOW, attempted_at=NOW)

    assert summary.resolution_rows_evaluated == 1
    assert summary.resolution_rows_with_config == 1
    assert summary.source_health_rows_observed == 1
    row = client.tables["Source_Health"][0]
    assert row["company_name"] == "Topgolf"
    assert row["platform"] == "phenom"
    assert row["source_url"] == "https://careers.topgolf.com/us/search-results"
    assert row["source_state"] == "manual_review_required"
    assert row["last_error_category"] == "unsupported_platform"
    assert row["attempt_count"] == 1


def test_resolution_source_health_refresh_is_idempotent_per_source():
    client = FakeSheetClient(
        {
            "Jobs": [job()],
            "Config_Companies": [config()],
            "Posting_Resolution": [resolution("not_found")],
            "Source_Health": [],
        }
    )

    refresh_source_health_from_resolutions(client, observed_at=NOW, attempted_at=NOW)
    refresh_source_health_from_resolutions(client, observed_at="2026-06-27T15:45:00Z", attempted_at=NOW)

    assert len(client.tables["Source_Health"]) == 1
    assert client.tables["Source_Health"][0]["attempt_count"] == 2
    assert client.tables["Source_Health"][0]["consecutive_failures"] == 2


def test_resolution_refresh_skips_unconfigured_companies_without_failing():
    client = FakeSheetClient(
        {
            "Jobs": [job(company="Unconfigured Co")],
            "Config_Companies": [],
            "Posting_Resolution": [resolution("not_found")],
            "Source_Health": [],
        }
    )

    summary = refresh_source_health_from_resolutions(client, observed_at=NOW, attempted_at=NOW)

    assert summary.resolution_rows_evaluated == 1
    assert summary.skipped_without_config == 1
    assert client.tables["Source_Health"] == []
