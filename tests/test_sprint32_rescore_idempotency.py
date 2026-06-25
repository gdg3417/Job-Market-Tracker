from pathlib import Path

from src.models import JobPosting
from src.rescore_jobs import rescore_jobs
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeSheetClient:
    def __init__(self, job):
        self.job = job
        self.updated = []

    def read_jobs_with_row_numbers(self):
        return [(2, self.job)]

    def read_records(self, worksheet_name):
        return []

    def update_job(self, row_number, job):
        self.updated.append((row_number, job))


def test_identical_rescore_does_not_rewrite_unchanged_job_row():
    rules = load_scoring_rules(RULES_PATH)
    job = JobPosting(
        job_key="already-scored",
        company="Example Industrial",
        title="Director, Commercial Strategy",
        location="Plano, TX",
        source_primary="company_site",
        canonical_url="https://careers.example.com/jobs/already-scored",
        description_text=(
            "Own revenue growth and pricing strategy, lead operating reviews, manage a team, "
            "and report to the business unit president."
        ),
        status="open",
        score_status="provisional",
    )
    score_job(job, rules)
    sheet = FakeSheetClient(job)

    result = rescore_jobs(
        sheet,
        rules,
        all_open=True,
        append_run=False,
    )

    assert result.jobs_selected == 1
    assert result.jobs_unchanged == 1
    assert result.jobs_would_update == 0
    assert result.jobs_updated == 0
    assert sheet.updated == []
