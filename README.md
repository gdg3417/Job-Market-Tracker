# Job Market Tracker

A zero added cost Python, GitHub Actions, and Google Sheets job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It is designed to monitor roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprint 1 scaffold is complete. Sprint 2 Google Sheets connectivity has been added as a smoke test.

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

## Next sprint

Sprint 3 defines the normalized job model and target profile format. Some model scaffolding already exists, but Sprint 3 should tighten the standard job fields, target profile config, normalization behavior, and unit tests.
