# Operations runbook

This runbook documents the current Job Market Tracker operating flow after Sprints 13 through 16.

## Local setup

Run from PowerShell in the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest
```

For a first-time setup, create `.env` from `.env.example` and point local credentials to files under the ignored `credentials/` folder.

Required Google Sheets environment values:

```text
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_APPLICATION_CREDENTIALS=credentials/google-credentials.json
```

Optional Gmail values:

```text
GMAIL_CLIENT_CONFIG=credentials/gmail-client-config.json
GMAIL_TOKEN_JSON=credentials/gmail-token.json
GMAIL_LABEL_NAME=Job Tracker
GMAIL_MAX_RESULTS=50
```

## Local validation sequence

Run this sequence before trusting an unattended workflow run:

```powershell
pytest
python -m src.schema --validate
python -m src.main --static-pages-smoke-test
python -m src.main --gmail-alerts-smoke-test
python -m src.main --job-upsert-smoke-test
python -m src.dashboard
```

Expected results:

1. `pytest` passes.
2. `python -m src.schema --validate` returns JSON with `ok: true`.
3. Static page ingestion does not recreate generic search rows.
4. Gmail ingestion does not recreate known alert metadata rows.
5. Dashboard and Digest write successfully.

## Manual GitHub Actions run

Use GitHub Actions when local validation is clean.

1. Open the repo in GitHub.
2. Go to Actions.
3. Select `Job Tracker Daily Run`.
4. Choose `Run workflow` on `main`.
5. Review the Step Summary when the run finishes.

The workflow should report:

```text
Static jobs found
Static jobs rejected
Gmail emails read
Gmail jobs accepted
Gmail alerts rejected
Dashboard rows written
Digest rows written
Final status
```

If Gmail secrets are missing, the workflow should skip Gmail ingestion cleanly. If required Google Sheets secrets are missing, the workflow should fail before ingestion.

## Schema validation and repair

Validate schema:

```powershell
python -m src.schema --validate
```

Repair headers and workbook timezone:

```powershell
python -m src.schema --repair-headers
```

Use repair when:

1. A worksheet header was edited manually.
2. A required worksheet such as `Rejected_Jobs` is missing.
3. The workbook timezone is not `America/Chicago`.
4. A write fails because expected headers are missing.

After repair, run:

```powershell
python -m src.schema --validate
python -m src.dashboard
```

## Dashboard and Digest refresh

Refresh Dashboard and Digest manually with:

```powershell
python -m src.dashboard
```

This reads `Jobs`, rewrites `Dashboard`, rewrites `Digest`, and appends a run record. If this command fails, review output is incomplete even if ingestion succeeded.

## Data quality cleanup

Use `Rejected_Jobs` as the first cleanup view.

Review these fields:

```text
source
title
company
url
rejection_reason
extraction_notes
raw_evidence
created_at
```

Recommended actions:

1. If the source is a job board search page, disable or reclassify the source in `Config_Companies`.
2. If the URL is a company career landing page, replace it with a direct ATS or posting path where available.
3. If the row is Gmail alert metadata, leave it rejected and improve parser rules only if good postings are also blocked.
4. If a valid direct posting was rejected, add a focused test before loosening the data quality gate.
5. Remove corresponding malformed rows from `Job_Sources` when cleaning old polluted `Jobs` rows.

Do not move rejected rows directly into `Jobs`. Rejected rows should either drive source cleanup or parser hardening.

## Source rules

Static page ingestion should prioritize target company career pages and direct posting URLs.

Do not use generic static scraping for:

```text
LinkedIn
Indeed
Google Jobs
Built In
The Ladders
near-me pages
search result pages
category browse pages
resume or profile pages
help or services pages
```

Preferred ingestion modes:

```text
greenhouse
lever
static_direct
gmail_only
manual_review_only
disabled
```

Sprint 18 should formalize source quality and ingestion mode fields in `Config_Companies` if they are not already present.

## Secrets and credential handling

Do not commit credentials.

GitHub required secrets:

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON
```

GitHub optional Gmail secrets:

```text
GMAIL_CLIENT_CONFIG
GMAIL_TOKEN_JSON
```

Local credential files should stay under `credentials/`. The daily workflow writes secret JSON values to temporary runner files and should not print credential contents.

## Failure handling

If schema validation fails, stop and repair the workbook before ingestion.

If static page rejected rows spike, audit `Config_Companies` first. The likely cause is a job board, search URL, category page, or JavaScript-heavy career site being treated as a static source.

If Gmail rejected rows exceed accepted jobs, inspect recent alert email structure and `Rejected_Jobs` before relaxing parser rules.

If Dashboard refresh fails, rerun `python -m src.dashboard` after resolving the error because Digest and Dashboard output may be stale.
