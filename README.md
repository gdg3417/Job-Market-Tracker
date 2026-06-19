# Job Market Tracker

A zero added cost Python, GitHub Actions, and Google Sheets job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It monitors roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 16 are implemented in code. The current system supports Google Sheets writes, Greenhouse and Lever ingestion, static company career page ingestion, Gmail alert ingestion, dedupe and lifecycle handling, Dashboard and Digest refresh, workbook schema validation, Gmail quarantine handling, final data quality gates, and a safer daily GitHub Actions workflow.

The daily workflow is safe to run only after the workbook schema validates. It runs tests, validates required secrets, writes credentials to temporary runner files, validates the workbook schema, records workflow validation, runs static career pages, runs Gmail ingestion when optional Gmail secrets exist, runs Greenhouse, Lever, and lifecycle handling, then refreshes Dashboard and Digest.

Important source rule: job boards should not be scraped as generic static pages. LinkedIn, Indeed, Google Jobs, Built In, and The Ladders should enter through Gmail alerts, explicit APIs, direct ATS posting URLs, or should be disabled. Static page ingestion should focus on reliable target company career pages and direct posting URLs.

## Repo structure

```text
job-market-tracker/
  README.md
  requirements.txt
  .gitignore
  .env.example
  config/
    scoring_rules.yml
    target_profile.yml
  docs/
    operations_runbook.md
    sprint_2_google_sheets_setup.md
    sprint_9_gmail_alert_setup.md
    sprint_15_data_quality_gates.md
    sprint_16_workflow_readiness.md
  src/
    __init__.py
    main.py
    settings.py
    models.py
    normalize.py
    dedupe.py
    lifecycle.py
    job_upsert.py
    scoring.py
    sheets.py
    digest.py
    dashboard.py
    data_quality.py
    schema.py
    workflow_validation.py
    companies.py
    sources/
      __init__.py
      greenhouse.py
      lever.py
      static_pages.py
      gmail_alerts.py
  tests/
    __init__.py
    test_dashboard.py
    test_data_quality.py
    test_dedupe.py
    test_gmail_alerts.py
    test_gmail_alerts_main.py
    test_job_upsert.py
    test_normalize.py
    test_schema.py
    test_scoring.py
    test_sheets.py
    test_static_pages.py
  .github/
    workflows/
      daily-run.yml
```

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

`Rejected_Jobs` captures records blocked by final quality gates. It is not a staging tab for good jobs. Rows in that tab should be reviewed for source quality issues, parser misses, or source URLs that should be disabled or corrected.

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

For macOS or Linux:

```bash
cd ~/Desktop
git clone https://github.com/gdg3417/Job-Market-Tracker.git
cd Job-Market-Tracker
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
pytest
```

## Core validation commands

Run these before trusting an unattended daily workflow run:

```powershell
pytest
python -m src.schema --validate
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
python -m src.dashboard
```

Use this command only when the workbook headers or timezone need repair:

```powershell
python -m src.schema --repair-headers
```

## Main commands

```powershell
python -m src.main --dry-run
python -m src.main --sheets-smoke-test
python -m src.main --greenhouse-smoke-test
python -m src.main --lever-smoke-test
python -m src.main --job-upsert-smoke-test
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
python -m src.dashboard
python -m src.workflow_validation
```

## GitHub Actions

The daily workflow is `.github/workflows/daily-run.yml`.

It supports manual runs with `workflow_dispatch` and scheduled runs around 06:30 AM Central. The workflow checks the Central schedule window so duplicate UTC cron entries do not both run during daylight saving changes.

Required secrets:

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON
```

Optional Gmail secrets:

```text
GMAIL_CLIENT_CONFIG
GMAIL_TOKEN_JSON
```

If optional Gmail secrets are missing, Gmail ingestion skips cleanly. Required Google Sheets secrets must exist or the workflow fails before ingestion.

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
| Sprint 17 | In progress | Documentation and runbook cleanup |

## Known limitations

Static HTML extraction misses JavaScript-rendered job boards and career sites. Some sources need ATS APIs, Gmail alerts, or manual URL correction.

Static extraction can produce false positives without strict guards, which is why data quality gates reject generic titles, navigation URLs, search URLs, and weak job board links.

Gmail alert parsing depends on email structure. If a sender changes its template, candidate jobs may be rejected or quarantined until parser logic is updated.

Salary often requires manual research. The tracker can store salary fields, but many postings omit compensation.

Commute is rules-based, not map-based. Commute estimates should be treated as directional until reviewed manually.

## Documentation

Operational procedures are in `docs/operations_runbook.md`.

Sprint details are in:

```text
docs/sprint_15_data_quality_gates.md
docs/sprint_16_workflow_readiness.md
```

The next planned implementation sprint is Sprint 18, source configuration cleanup.