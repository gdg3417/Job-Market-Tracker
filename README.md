# Job Market Tracker

A zero added cost Python, GitHub Actions, and Google Sheets job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It is designed to monitor roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 9 are implemented in code. Sprint 9 adds Gmail alert ingestion so labeled job alert emails can flow into `Jobs` and `Job_Sources` without scraping LinkedIn, Indeed, Google Jobs, or other job boards directly.

The repo currently contains:

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
    sprint_2_google_sheets_setup.md
    sprint_9_gmail_alert_setup.md
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
    companies.py
    sources/
      __init__.py
      greenhouse.py
      lever.py
      static_pages.py
      gmail_alerts.py
  tests/
    __init__.py
    test_normalize.py
    test_dedupe.py
    test_job_upsert.py
    test_scoring.py
    test_sheets.py
    test_gmail_alerts.py
  .github/
    workflows/
      daily-run.yml
```

## Local Windows setup

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
python -m src.main --dry-run
pytest
```

If script execution is blocked in PowerShell, run this once for the current user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Local macOS or Linux setup

```bash
cd ~/Desktop
git clone https://github.com/gdg3417/Job-Market-Tracker.git
cd Job-Market-Tracker
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python -m src.main --dry-run
pytest
```

## Sprint 1 dry-run smoke test

Sprint 1 runs a local dry-run only. It normalizes one sample job, applies the starter scoring rules, and prints a JSON result.

```bash
python -m src.main --dry-run
```

Expected behavior: the script prints a sample job key, total score, alert tier, role family, and role level. It does not connect to Google Sheets.

## Sprint 2 Google Sheets smoke test

Sprint 2 reads from the tracker Sheet and appends a test row to `Runs`.

Before running it, complete the setup guide:

```text
docs/sprint_2_google_sheets_setup.md
```

Required local environment variables:

```text
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_APPLICATION_CREDENTIALS=credentials/google-credentials.json
```

Run the smoke test from the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main --sheets-smoke-test
```

Expected behavior: the script reads `Config_Companies`, reads `Config_Searches`, appends one row to `Runs`, and prints a JSON result with row counts.

## Sprint 5 Greenhouse smoke test

Sprint 5 reads active Greenhouse company rows, fetches public Greenhouse jobs, normalizes and scores them, and appends source-level run records to `Runs`.

```powershell
python -m src.main --greenhouse-smoke-test
```

Expected behavior: the script prints source results and the top scored Greenhouse jobs. This command does not upsert jobs into `Jobs`.

## Sprint 6 Lever smoke test

Sprint 6 reads active Lever company rows, fetches public Lever jobs, normalizes and scores them, and appends source-level run records to `Runs`.

```powershell
python -m src.main --lever-smoke-test
```

Expected behavior: the script prints source results and the top scored Lever jobs. This command does not upsert jobs into `Jobs`.

## Sprint 7 job upsert and Sprint 8 lifecycle smoke test

This command fetches Greenhouse and Lever jobs, dedupes them against existing `Jobs` and `Job_Sources` rows, inserts or updates records, and then updates lifecycle status for jobs not seen in the run.

```powershell
python -m src.main --job-upsert-smoke-test
```

Expected behavior:

1. Existing jobs are loaded from `Jobs`.
2. Existing source links are loaded from `Job_Sources`.
3. New jobs are appended to `Jobs`.
4. Existing jobs update `last_seen_date`, scoring fields, and current job details.
5. Same-source repeats update the existing `Job_Sources` row.
6. Same job from a second source creates a second `Job_Sources` row without creating a second `Jobs` row.
7. Missing jobs move through `not_seen_once`, `likely_closed`, and `confirmed_closed` when closure evidence exists.
8. Sprint 7 and Sprint 8 summary rows are appended to `Runs`.

## Sprint 9 Gmail alert ingestion

Sprint 9 reads emails labeled `Job Tracker`, extracts job title, company, location, URL, source job ID, and received date, then upserts the extracted jobs into `Jobs` and `Job_Sources`.

Before running it, complete the setup guide:

```text
docs/sprint_9_gmail_alert_setup.md
```

Optional local environment variables:

```text
GMAIL_CLIENT_CONFIG=credentials/gmail-client-config.json
GMAIL_TOKEN_JSON=credentials/gmail-token.json
GMAIL_LABEL_NAME=Job Tracker
GMAIL_MAX_RESULTS=50
```

Run the Gmail ingestion smoke test from the repo root:

```powershell
python -m src.main --gmail-alerts-smoke-test
```

Expected behavior:

1. Gmail messages with the configured label are read.
2. Alert emails are parsed into candidate jobs.
3. Extracted jobs are normalized and scored.
4. Jobs are upserted into `Jobs` and `Job_Sources` with `source_primary` set to `gmail_alert`.
5. Low-confidence extractions are flagged with `manual_review_required` in `description_text`.
6. A Sprint 9 summary row is appended to `Runs`.

## Credential handling

Do not commit any credentials.

The `.gitignore` excludes:

```text
.env
credentials/
*.credentials.json
*service-account*.json
google-credentials.json
client_secret*.json
token.json
.gmail_token.json
```

The Google Sheets service account JSON should stay local under `credentials/` and should never be pushed to GitHub. The service account should be shared only on the Job Market Tracker Sheet.

The Gmail OAuth client JSON and Gmail token JSON should also stay local under `credentials/`. Do not reuse the Sheets service account for Gmail inbox access.

## Acceptance criteria status

| Sprint | Criterion | Status |
|---|---|---|
| Sprint 1 | Repo exists on GitHub | Complete |
| Sprint 1 | Python script runs locally | Scaffolded through `python -m src.main --dry-run` |
| Sprint 1 | `requirements.txt` installs cleanly | Scaffolded and validated in local container |
| Sprint 1 | `.gitignore` excludes credentials, cache files, and local databases | Complete |
| Sprint 1 | GitHub repo contains no credentials | Complete |
| Sprint 2 | Python can read from `Config_Companies` | Implemented, pending local Google credential setup |
| Sprint 2 | Python can read from `Config_Searches` | Implemented, pending local Google credential setup |
| Sprint 2 | Python can append a row to `Runs` | Implemented, pending local Google credential setup |
| Sprint 2 | Credentials are not committed | Complete |
| Sprint 2 | Service account has access only to the tracker Sheet | Manual setup step |
| Sprint 5 | Greenhouse jobs can be fetched, normalized, scored, and logged | Implemented |
| Sprint 6 | Lever jobs can be fetched, normalized, scored, and logged | Implemented |
| Sprint 7 | Same job found twice does not create duplicate `Jobs` rows | Implemented and unit tested |
| Sprint 7 | Same job from two sources creates one `Jobs` row and two `Job_Sources` rows | Implemented and unit tested |
| Sprint 7 | Existing open jobs update `last_seen_date` | Implemented and unit tested |
| Sprint 7 | New jobs get `first_seen_date` | Implemented |
| Sprint 7 | Dedupe logic is unit tested | Implemented |
| Sprint 8 | Missing jobs are not immediately marked closed | Implemented |
| Sprint 8 | Jobs missing twice become `likely_closed` | Implemented |
| Sprint 8 | URL closure checks are guarded from run-breaking errors | Implemented |
| Sprint 9 | Gmail job alert emails can flow into the tracker | Implemented |
| Sprint 9 | Duplicate alert emails do not duplicate jobs | Uses existing Sprint 7 upsert logic |
| Sprint 9 | Extracted URLs are captured | Implemented |
| Sprint 9 | Received date becomes `first_seen_date` for new jobs | Implemented |
| Sprint 9 | Bad extractions are flagged for review | Implemented with low confidence and `manual_review_required` |

## Next sprint

Sprint 10 adds static company career page support for target companies where Greenhouse, Lever, and Gmail alerts do not provide enough coverage.
