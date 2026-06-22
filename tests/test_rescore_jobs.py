from pathlib import Path

from src.models import JobPosting
from src.scoring import load_scoring_rules
from src.rescore_jobs import rescore_open_gmail_jobs


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeSheetClient:
    def __init__(self, jobs):
        self.jobs = jobs
        self.updated = []
        self.runs = []

    def read_jobs_with_row_numbers(self):
        return list(self.jobs)

    def update_job(self, row_number, job):
        self.updated.append((row_number, job))

    def append_run(self, record):
        self.runs.append(record)


def make_job(**overrides):
    values = {
        "job_key": "gmail-job",
        "company": "Topgolf",
        "title": "Sr Manager, Strategic Planning",
        "location": "Dallas, TX",
        "source_primary": "gmail_alert",
        "description_text": "Extracted from Gmail job alert. confidence=high. origin=linkedin; extraction=linkedin_digest_card; linkedin_job_id=4417965465",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-20",
        "status": "open",
        "remote_status": "unknown",
        "work_model": "unknown",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_rescore_updates_only_open_gmail_jobs_and_refreshes_digest(monkeypatch):
    rules = load_scoring_rules(RULES_PATH)
    sheet = FakeSheetClient(
        [
            (2, make_job()),
            (3, make_job(job_key="closed", status="confirmed_closed")),
            (4, make_job(job_key="manual", source_primary="manual")),
            (
                5,
                make_job(
                    job_key="complete",
                    company="Acme",
                    title="Director, Commercial Strategy",
                    location="Plano, TX Hybrid",
                    salary_min=180000,
                    salary_max=220000,
                    total_comp_estimate=220000,
                    remote_status="hybrid",
                    work_model="hybrid",
                    description_text="Own revenue growth, pricing, margin expansion, and executive operating reviews.",
                ),
            ),
        ]
    )
    refresh_calls = []

    def fake_refresh(sheet_client, *, append_run=True, as_of=None):
        refresh_calls.append((sheet_client, append_run, as_of))
        return object()

    monkeypatch.setattr("src.rescore_jobs.apply_dashboard_and_digest", fake_refresh)
    result = rescore_open_gmail_jobs(sheet, rules)

    assert result.jobs_read == 4
    assert result.gmail_open_jobs == 2
    assert result.jobs_updated == 2
    assert result.manual_review_jobs == 1
    assert result.dashboard_refreshed is True
    assert [row_number for row_number, _ in sheet.updated] == [2, 5]
    assert "review_reason=sparse_gmail_high_signal_title" in sheet.updated[0][1].score_explanation
    assert "manual_review=true" not in sheet.updated[1][1].score_explanation
    assert refresh_calls == [(sheet, False, None)]
    assert len(sheet.runs) == 1
    assert sheet.runs[0]["run_type"] == "sprint_22_sparse_gmail_rescore"
    assert sheet.runs[0]["records_updated"] == 2


def test_rescore_can_skip_dashboard_refresh_and_run_log(monkeypatch):
    rules = load_scoring_rules(RULES_PATH)
    sheet = FakeSheetClient([(2, make_job())])

    def unexpected_refresh(*args, **kwargs):
        raise AssertionError("Dashboard refresh should not run")

    monkeypatch.setattr("src.rescore_jobs.apply_dashboard_and_digest", unexpected_refresh)
    result = rescore_open_gmail_jobs(
        sheet,
        rules,
        refresh_dashboard=False,
        append_run=False,
    )

    assert result.jobs_updated == 1
    assert result.dashboard_refreshed is False
    assert sheet.runs == []
