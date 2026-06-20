# Job Market Tracker

A zero added cost Python, GitHub Actions, Google Sheets, and Google Apps Script job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It monitors roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 20 are implemented in code.

The current system supports:

1. Google Sheets writes
2. Greenhouse and Lever ingestion
3. Static company career page ingestion
4. Gmail alert ingestion
5. Dedupe and lifecycle handling
6. Dashboard and Digest refresh
7. Workbook schema validation
8. Gmail quarantine handling
9. Final data quality gates
10. A safer daily GitHub Actions workflow
11. Source configuration auditing
12. Focused scoring for passive job monitoring
13. A plain-English executive Dashboard
14. A Google Apps Script weekly email digest

Sprint 20 redesigns the Dashboard so it is an action-oriented executive summary instead of a fragile formula page. It also adds a bound Apps Script weekly digest email that can be scheduled for Monday around 8:00 AM Central.

## Repo structure

```text
job-market-tracker/
  apps_script/
    weekly_digest_email.gs
  config/
    scoring_rules.yml
    target_profile.yml
  docs/
    operations_runbook.md
    sprint_20_weekly_email_dashboard.md
  src/
    dashboard.py
  tests/
    test_dashboard.py
```

Other source, test, config, and workflow files remain in their existing locations.

## Workbook structure

The canonical workbook schema is managed in `src/schema.py`.

Required tabs:

```text
Config_Searches
Config_Companies
Scoring_Rules
Target_Companies
Jobs
Job_Sources
Rejected_Jobs
Snapshots
Runs
Digest
Dashboard
```

`Rejected_Jobs` captures records blocked by final quality gates. It is not a staging tab for good jobs.

## Local setup

Run these commands from PowerShell.

```powershell
cd $env:USERPROFILE\Desktop
git clone https://github.com/gdg3417/Job-Market-Tracker.git
cd Job-Market-Tracker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
pytest
```

If script execution is blocked in PowerShell, run this once for the current user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Core validation commands

Run these before trusting an unattended daily workflow run:

```powershell
pytest
python -m src.schema --validate
python -m src.source_audit
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
python -m src.dashboard
```

Use this command only when the workbook headers or timezone need repair:

```powershell
python -m src.schema --repair-headers
```

## Weekly email digest

Sprint 20 adds `apps_script/weekly_digest_email.gs` for a bound Google Apps Script weekly digest.

Setup is documented in:

```text
docs/sprint_20_weekly_email_dashboard.md
```

The weekly email reads the `Digest` tab, sends through Apps Script, and can be scheduled for Monday around 8:00 AM Central. It should not be run from the daily GitHub Actions workflow.

## Main commands

```powershell
python -m src.main --dry-run
python -m src.main --sheets-smoke-test
python -m src.main --greenhouse-smoke-test
python -m src.main --lever-smoke-test
python -m src.main --job-upsert-smoke-test
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
python -m src.source_audit
python -m src.source_audit --apply-recommendations
python -m src.dashboard
python -m src.workflow_validation
```

## GitHub Actions

The daily workflow is `.github/workflows/daily-run.yml`.

It supports manual runs with `workflow_dispatch` and scheduled runs around 06:30 AM Central. The workflow checks the Central schedule window so duplicate UTC cron entries do not both run during daylight saving changes.

## Sprint implementation status

| Sprint | Status | Main addition |
| --- | --- | --- |
| Sprint 1 | Complete | Local Python scaffold and dry-run |
| Sprint 2 | Complete | Google Sheets smoke test and `Runs` write |
| Sprint 3 | Complete | Config and scoring foundation |
| Sprint 4 | Complete | Target profile and search configuration cleanup |
| Sprint 5 | Complete | Greenhouse source support |
| Sprint 6 | Complete | Lever source support |
| Sprint 7 | Complete | Job upsert and dedupe into `Jobs` and `Job_Sources` |
| Sprint 8 | Complete | Lifecycle handling for missing and closed jobs |
| Sprint 9 | Complete | Gmail alert ingestion |
| Sprint 10 | Complete | Static company career page ingestion |
| Sprint 11 | Complete | Dashboard and Digest generation |
| Sprint 12 | Complete | GitHub Actions daily workflow foundation |
| Sprint 13 | Complete | Workbook schema validation and repair |
| Sprint 14 | Complete | Gmail quarantine and cleanup handling |
| Sprint 15 | Complete | Final data quality gates and `Rejected_Jobs` capture |
| Sprint 16 | Complete | Workflow automation readiness and safer daily run ordering |
| Sprint 17 | Complete | Documentation and runbook cleanup |
| Sprint 18 | Complete | Source configuration audit, source quality fields, and ingestion mode recommendations |
| Sprint 19 | Complete | Scoring tuning and focused Digest sections for weekly review usefulness |
| Sprint 20 | Complete | Plain-English Dashboard and Apps Script weekly email digest |
