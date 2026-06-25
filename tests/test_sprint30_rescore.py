from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import src.rescore_jobs as rescore_module
from src.models import JobPosting
from src.rescore_jobs import RescoreJobsResult, rescore_jobs
from src.scoring import load_scoring_rules


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeSheetClient:
    def __init__(self, jobs, config_rows=None, target_rows=None):
        self.jobs = list(jobs)
        self.config_rows = list(config_rows or [])
        self.target_rows = list(target_rows or [])
        self.updated = []
        self.runs = []

    def read_jobs_with_row_numbers(self):
        return list(self.jobs)

    def read_records(self, worksheet_name):
        if worksheet_name == "Config_Companies":
            return self.config_rows or [
                {
                    "company_name": "Acme Industrial",
                    "industry_bucket": "manufacturing",
                    "career_domain": "careers.acme.com",
                }
            ]
        if worksheet_name == "Target_Companies":
            return self.target_rows
        return []

    def update_job(self, row_number, job):
        self.updated.append((row_number, job))

    def append_run(self, row):
        self.runs.append(row)


def _job(job_key: str, status: str, score_status: str, *, company: str = "Acme Industrial") -> JobPosting:
    return JobPosting(
        job_key=job_key,
        company=company,
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


def test_rescore_company_filter_matches_configured_aliases():
    rules = load_scoring_rules(RULES_PATH)
    sheet = FakeSheetClient(
        [(2, _job("toyota", "open", "provisional", company="Toyota North America"))],
        config_rows=[
            {
                "company_name": "Toyota Motor North America",
                "canonical_company_name": "Toyota Motor North America",
                "company_aliases": "Toyota North America; Toyota",
                "industry_bucket": "manufacturing",
                "career_domain": "careers.toyota.com",
            }
        ],
    )

    result = rescore_jobs(
        sheet,
        rules,
        company="Toyota Motor North America",
        append_run=False,
    )

    assert result.jobs_selected == 1
    assert result.jobs_updated == 1


def _cli_args(**overrides):
    values = {
        "provisional_only": False,
        "verified_only": False,
        "job_key": None,
        "company": None,
        "all_open": False,
        "dry_run": False,
        "refresh_dashboard": False,
        "no_refresh": False,
        "no_run_log": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_main_without_selectors_preserves_legacy_gmail_scope_and_dashboard_refresh(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(rescore_module, "parse_args", lambda: _cli_args())
    monkeypatch.setattr(rescore_module, "load_settings", lambda: SimpleNamespace(scoring_rules_path=RULES_PATH))
    monkeypatch.setattr(rescore_module, "load_scoring_rules", lambda path: {})
    monkeypatch.setattr(rescore_module, "SheetClient", SimpleNamespace(from_settings=lambda settings: object()))

    def fake_rescore(sheet_client, rules, **kwargs):
        captured.update(kwargs)
        return RescoreJobsResult()

    monkeypatch.setattr(rescore_module, "rescore_jobs", fake_rescore)

    rescore_module.main()

    assert captured["gmail_only"] is True
    assert captured["all_open"] is False
    assert captured["refresh_dashboard"] is True
    assert '"run_mode": "sprint_26_potential_priority_rescore"' in capsys.readouterr().out


def test_main_all_open_uses_sprint30_scope_without_implicit_dashboard_refresh(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(rescore_module, "parse_args", lambda: _cli_args(all_open=True))
    monkeypatch.setattr(rescore_module, "load_settings", lambda: SimpleNamespace(scoring_rules_path=RULES_PATH))
    monkeypatch.setattr(rescore_module, "load_scoring_rules", lambda path: {})
    monkeypatch.setattr(rescore_module, "SheetClient", SimpleNamespace(from_settings=lambda settings: object()))

    def fake_rescore(sheet_client, rules, **kwargs):
        captured.update(kwargs)
        return RescoreJobsResult()

    monkeypatch.setattr(rescore_module, "rescore_jobs", fake_rescore)

    rescore_module.main()

    assert captured["gmail_only"] is False
    assert captured["all_open"] is True
    assert captured["refresh_dashboard"] is False
    assert '"run_mode": "sprint_30_verified_rescore"' in capsys.readouterr().out
