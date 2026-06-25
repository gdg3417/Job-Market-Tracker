from pathlib import Path

from src.models import JobPosting
from src.rescore_jobs import rescore_jobs
from src.scoring import load_scoring_rules


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeSheetClient:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.updated = []
        self.runs = []

    def read_jobs_with_row_numbers(self):
        return list(self.jobs)

    def read_records(self, worksheet_name):
        if worksheet_name == "Config_Companies":
            return [
                {
                    "company_name": "Acme Industrial",
                    "industry_bucket": "manufacturing",
                    "career_domain": "careers.acme.com",
                }
            ]
        return []

    def update_job(self, row_number, job):
        self.updated.append((row_number, job))

    def append_run(self, row):
        self.runs.append(row)


def _job(job_key: str, status: str, score_status: str) -> JobPosting:
    return JobPosting(
        job_key=job_key,
        company="Acme Industrial",
        title="Director, Commercial Strategy",
        location="Plano, TX",
        source_primary="company_site",
        canonical_url=f"https://careers.acme.com/jobs/{job_key}",
        description_text=(
            "Responsibilities include owning revenue growth, pricing strategy, and operating reviews. Qualifications include "
            "a bachelor's degree and ten years of experience. Lead a team and report to the business unit president."
        ),
        status=status,
        score_status=score_status,
    )


def test_rescore_dry_run_filters_provisional_jobs_without_writes():
    rules = load_scoring_rules(RULES_PATH)
    sheet = FakeSheetClient(
        [
            (2, _job("provisional", "open", "provisional")),
            (3, _job("verified", "open", "verified")),
            (4, _job("closed", "confirmed_closed", "provisional")),
        ]
    )

    result = rescore_jobs(
        sheet,
        rules,
        provisional_only=True,
        all_open=True,
        dry_run=True,
        refresh_dashboard=True,
    )

    assert result.jobs_selected == 1
    assert result.jobs_would_update == 1
    assert result.jobs_updated == 0
    assert result.dashboard_refreshed is False
    assert sheet.updated == []
    assert sheet.runs == []


def test_rescore_job_key_can_target_one_exact_row():
    rules = load_scoring_rules(RULES_PATH)
    sheet = FakeSheetClient(
        [
            (2, _job("one", "open", "provisional")),
            (3, _job("two", "open", "provisional")),
        ]
    )

    result = rescore_jobs(sheet, rules, job_key="two", append_run=False)

    assert result.jobs_selected == 1
    assert result.jobs_updated == 1
    assert sheet.updated[0][0] == 3
