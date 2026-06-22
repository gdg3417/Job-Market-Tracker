# Operations runbook

This runbook documents the current Job Market Tracker operating flow after Sprint 21.

## Local setup

Run from PowerShell in the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest
```

For first-time setup, create `.env` from `.env.example` and point local credential values to files under the ignored `credentials/` folder.

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
5. LinkedIn digest cards retain the correct title, company, location, and canonical direct URL.
6. Dashboard and Digest write successfully.
7. The Dashboard top section gives a clear answer.
8. The Dashboard has no `#REF!` or `#VALUE!`.

## Manual GitHub Actions run

Use GitHub Actions when local validation is clean.

1. Open the repo in GitHub.
2. Go to Actions.
3. Select `Job Tracker Daily Run`.
4. Choose `Run workflow` on `main`.
5. Review the Step Summary when the run finishes.

The workflow should report source counts, Gmail counts when available, Dashboard rows written, Digest rows written, and final status.

If optional Gmail configuration is missing, Gmail ingestion should skip cleanly. Required Google Sheets configuration must exist or the workflow fails before ingestion.

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

This reads `Jobs`, `Target_Companies`, `Config_Companies`, `Rejected_Jobs`, and `Runs`, then rewrites `Dashboard` and `Digest` and appends a run record.

The Sprint 20 Dashboard writes plain values, not spreadsheet formulas. It should show:

1. Executive answer
2. Action queue
3. Tracker health
4. Source health
5. Top roles to review
6. Source cleanup queue

The executive answer can be:

1. `Review roles now`
2. `Review strong fits this week`
3. `Review target company roles`
4. `Source cleanup needed`
5. `No action needed this week`

## Weekly email digest

The weekly email is handled by a bound Google Apps Script file:

```text
apps_script/weekly_digest_email.gs
```

Setup instructions are in:

```text
docs/sprint_20_weekly_email_dashboard.md
```

The email reads the `Digest` tab and sends a weekly summary. It is not part of the daily GitHub Actions workflow.

Recommended trigger:

```text
Monday around 8:00 AM Central
```

Manual Sheet menu:

```text
Job Tracker
  Send test weekly digest
  Send weekly digest now
```

## Weekly review process

1. Open the Dashboard.
2. Read `This week's answer`.
3. If it says `Review roles now`, inspect Immediate review rows the same day.
4. If it says `Review strong fits this week`, review Strong fit and Target company watchlist rows during weekly review.
5. If it says `Source cleanup needed`, inspect Source cleanup queue and `Rejected_Jobs`.
6. If it says `No action needed this week`, no job review is required.

## LinkedIn digest parsing

LinkedIn digest emails are parsed from both available MIME alternatives. When the same LinkedIn job ID appears in both, the parser retains the more complete valid card. Plain text or HTML can be used independently when only one contains direct postings.

A valid card should contain:

```text
Job title
Company
Location, when present
Direct LinkedIn posting URL
```

Accepted LinkedIn URLs are canonicalized to:

```text
https://www.linkedin.com/jobs/view/<job_id>
```

The source ID must be:

```text
linkedin-<job_id>
```

This source ID must not include the Gmail message ID or the URL position in the email.

Do not create job records from:

```text
See all jobs
LinkedIn search pages
Premium offers
Manage alerts
Unsubscribe
Help pages
Feed or navigation links
Messaging links
My Network links
Notification links
```

A malformed card should create an individual rejected alert when a direct posting ID is present. It must not cause the other valid cards in the same email to be discarded.

LinkedIn job alert confirmation emails remain quarantined as `linkedin_job_alert_confirmation`.

Sprint 21 validation fixtures:

```text
tests/fixtures/linkedin_topgolf.eml
tests/fixtures/linkedin_toyota.eml
```

Do not run a Gmail production backfill during Sprint 21. Backlog release remains part of the later Gmail reliability sprint.

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

## Failure handling

If schema validation fails, stop and repair the workbook before ingestion.

If static page rejected rows spike, audit `Config_Companies` first. The likely cause is a job board, search URL, category page, or JavaScript-heavy career site being treated as a static source.

If Gmail rejected rows exceed accepted jobs, inspect recent alert email structure and `Rejected_Jobs` before relaxing parser rules.

If multiple LinkedIn posting URLs receive the same title or company, confirm the digest path was detected and inspect both MIME alternatives and their card boundaries. Do not fix this by falling back to the email subject.

If a LinkedIn posting is duplicated across alert emails, confirm its source ID uses only the LinkedIn job ID.

If Dashboard refresh fails, rerun `python -m src.dashboard` after resolving the error because Digest and Dashboard output may be stale.

If the weekly email does not arrive, run `Send test weekly digest` from the Sheet menu and then inspect Apps Script executions and triggers.
