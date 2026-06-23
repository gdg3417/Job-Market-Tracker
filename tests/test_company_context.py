from pathlib import Path

from src.company_context import build_company_context_map, company_context_for_name
from src.job_upsert import _apply_workbook_company_context_to_gmail_jobs
from src.models import JobPosting
from src.rescore_jobs import rescore_open_gmail_jobs
from src.scoring import load_scoring_rules


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeSheetClient:
    def __init__(self, jobs, config_rows=None, target_rows=None):
        self.jobs = jobs
        self.config_rows = list(config_rows or [])
        self.target_rows = list(target_rows or [])
        self.updated = []

    def read_jobs_with_row_numbers(self):
        return list(self.jobs)

    def read_records(self, worksheet_name):
        if worksheet_name == "Config_Companies":
            return list(self.config_rows)
        if worksheet_name == "Target_Companies":
            return list(self.target_rows)
        return []

    def update_job(self, row_number, job):
        self.updated.append((row_number, job))


def _gmail_strategy_job() -> JobPosting:
    return JobPosting(
        job_key="acme-strategy",
        company="Acme Industrial",
        title="Senior Manager, Commercial Strategy",
        location="Dallas, TX",
        source_primary="gmail_alert",
        canonical_url="https://example.com/jobs/acme-strategy",
        description_text=(
            "Extracted from Gmail job alert. confidence=high. origin=linkedin; "
            "extraction=linkedin_digest_card; linkedin_job_id=1234567890"
        ),
        remote_status="unknown",
        work_model="unknown",
        status="open",
    )


def _context_sheet(jobs=None) -> FakeSheetClient:
    return FakeSheetClient(
        jobs or [],
        config_rows=[{"company_name": "Acme Industrial", "industry_bucket": "manufacturing"}],
        target_rows=[{"company_name": "Acme Industrial", "priority_tier": "Tier 1", "score_boost_points": "5"}],
    )


def test_company_context_map_merges_config_and_target_rows_without_blank_overwrite():
    contexts = build_company_context_map(
        [
            {
                "company_name": "Acme Industrial",
                "parent_company": "Acme Holdings",
                "industry_bucket": "manufacturing",
                "source_quality": "high",
            }
        ],
        [
            {
                "company_name": "Acme Industrial",
                "priority_tier": "Tier 1",
                "score_boost_points": "5",
                "industry_bucket": "",
            }
        ],
    )

    company = company_context_for_name("Acme Industrial", contexts)
    parent = company_context_for_name("Acme Holdings", contexts)

    assert company is not None
    assert company["industry_bucket"] == "manufacturing"
    assert company["priority_tier"] == "Tier 1"
    assert company["score_boost_points"] == "5"
    assert parent is not None
    assert parent["industry_bucket"] == "manufacturing"


def test_gmail_rescore_applies_workbook_target_company_context():
    rules = load_scoring_rules(RULES_PATH)
    job = _gmail_strategy_job()
    sheet = _context_sheet([(2, job)])

    result = rescore_open_gmail_jobs(
        sheet,
        rules,
        refresh_dashboard=False,
        append_run=False,
    )

    assert result.jobs_updated == 1
    scored = sheet.updated[0][1]
    assert scored.potential_priority == "high"
    assert "target_company=5" in scored.potential_priority_reason
    assert "company=20 (manufacturing)" in scored.potential_priority_reason


def test_gmail_upsert_boundary_applies_and_caches_workbook_company_context():
    sheet = _context_sheet()
    job = _gmail_strategy_job()

    scored_jobs = _apply_workbook_company_context_to_gmail_jobs(sheet, [job])

    assert len(scored_jobs) == 1
    scored = scored_jobs[0]
    assert scored.potential_priority == "high"
    assert "target_company=5" in scored.potential_priority_reason
    assert "company=20 (manufacturing)" in scored.potential_priority_reason
    assert sheet._gmail_company_contexts
    assert sheet._gmail_scoring_rules
