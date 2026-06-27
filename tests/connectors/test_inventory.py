from __future__ import annotations

from src.connectors.inventory import build_platform_inventory
from src.models import JobPosting
from src.resolution.models import PostingResolution
from src.source_reliability import SourceHealthState


class FakeSheetClient:
    def __init__(self, tables):
        self.tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables.get(worksheet_name, [])]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables.get(worksheet_name, []))]

    def read_jobs_with_row_numbers(self):
        return [(index + 2, JobPosting.from_dict(row)) for index, row in enumerate(self.tables.get("Jobs", []))]


def job(job_key, company, priority="high"):
    return JobPosting(
        job_key=job_key,
        company=company,
        title="Director, Strategy",
        location="Dallas, TX",
        status="open",
        potential_priority=priority,
        potential_priority_score=90,
        score_status="provisional",
    ).to_dict()


def test_platform_inventory_ranks_priority_structured_platforms_and_unresolved_jobs():
    client = FakeSheetClient(
        {
            "Config_Companies": [
                {
                    "company_id": "example",
                    "company_name": "Example Co",
                    "canonical_company_name": "Example Co",
                    "ats_platform": "greenhouse",
                    "ats_board_token": "example",
                    "career_search_url": "https://boards.greenhouse.io/example",
                    "enrichment_active": True,
                },
                {
                    "company_id": "phenom-co",
                    "company_name": "Phenom Co",
                    "canonical_company_name": "Phenom Co",
                    "ats_platform": "phenom",
                    "career_search_url": "https://careers.example.com/search-results",
                    "enrichment_active": True,
                },
            ],
            "Target_Companies": [
                {"company_name": "Example Co", "priority_tier": "tier 1", "active": True},
                {"company_name": "Phenom Co", "priority_tier": "tier 2", "active": True},
            ],
            "Jobs": [job("job-example", "Example Co"), job("job-phenom", "Phenom Co")],
            "Posting_Resolution": [PostingResolution(job_key="job-example", resolution_state="not_found").to_dict()],
            "Source_Health": [
                SourceHealthState(
                    company_id="example",
                    company_name="Example Co",
                    platform="greenhouse",
                    attempt_count=4,
                    success_count=3,
                    failure_count=1,
                    jobs_found=20,
                    jobs_accepted=5,
                ).to_dict()
            ],
        }
    )

    inventory = build_platform_inventory(client)
    rows = {row["platform"]: row for row in inventory["platforms_ranked"]}

    assert rows["greenhouse"]["connector_scope"] == "structured"
    assert rows["greenhouse"]["tier_1_company_count"] == 1
    assert rows["greenhouse"]["unresolved_high_potential_jobs"] == 1
    assert rows["phenom"]["connector_scope"] == "configured_only"
    assert "greenhouse" in inventory["selected_connector_scope"]
    assert inventory["platform_health"]["greenhouse"]["jobs_returned"] == 20


def test_platform_inventory_handles_empty_workbook():
    inventory = build_platform_inventory(FakeSheetClient({}))

    assert "platforms_ranked" in inventory
    assert isinstance(inventory["platforms_ranked"], list)
