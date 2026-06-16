# Job Market Tracker

A zero added cost Python, GitHub Actions, and Google Sheets job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It is designed to monitor roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Sprint 1 status

This repo contains the starter Python scaffold:

```text
job-market-tracker/
  README.md
  requirements.txt
  .gitignore
  .env.example
  config/
    scoring_rules.yml
    target_profile.yml
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

## Current smoke test

Sprint 1 runs a local dry-run only. It normalizes one sample job, applies the starter scoring rules, and prints a JSON result.

```bash
python -m src.main --dry-run
```

Expected behavior: the script prints a sample job key, total score, alert tier, role family, and role level. It does not connect to Google Sheets yet.

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

Sprint 2 will add Google Sheets setup using a service account. The service account JSON should stay local under `credentials/` and should never be pushed to GitHub.

## Sprint 1 acceptance criteria

| Criterion | Status |
|---|---|
| Repo exists on GitHub | Complete |
| Python script runs locally | Scaffolded through `python -m src.main --dry-run` |
| `requirements.txt` installs cleanly | Scaffolded and validated in local container |
| `.gitignore` excludes credentials, cache files, and local databases | Complete |
| GitHub repo contains no credentials | Complete |

## Next sprint

Sprint 2 connects Python to Google Sheets. The next work items are:

1. Set up a Google Cloud project.
2. Enable the Google Sheets API.
3. Create a service account.
4. Download the JSON locally under `credentials/`.
5. Share the Google Sheet with the service account email.
6. Add `GOOGLE_SHEET_ID` and `GOOGLE_APPLICATION_CREDENTIALS` to `.env`.
7. Read `Config_Companies` and `Config_Searches`.
8. Append a test row to `Runs`.
