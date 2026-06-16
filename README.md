# Job Market Tracker

A zero added cost Python, GitHub Actions, and Google Sheets job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It is designed to monitor roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 7 are implemented in code. Sprint 7 adds deduplication and upsert logic so Greenhouse and Lever jobs can flow into `Jobs` and `Job_Sources` without creating repeat job rows.

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
  src/
    __init__.py
    main.py
    settings.py
    models.py
    normalize.py
    dedupe.py
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

## Sprint 7 job upsert smoke test

Sprint 7 fetches Greenhouse and Lever jobs, dedupes them against existing `Jobs` and `Job_Sources` rows, then inserts or updates records.

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
7. A Sprint 7 summary row is appended to `Runs`.

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

The service account JSON should stay local under `credentials/` and should never be pushed to GitHub. The service account should be shared only on the Job Market Tracker Sheet.

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

## Next sprint

Sprint 8 adds lifecycle tracking and closure detection. It should increment `missed_count`, mark jobs as `not_seen_once` or `likely_closed`, calculate `days_open`, and handle reopened postings.
