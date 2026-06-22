# Operations runbook

This runbook documents the Job Market Tracker operating flow after Sprint 23.

## Local setup

Run from PowerShell in the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest
```

For first-time setup, create `.env` from `.env.example` and point local credential values to files under the ignored `credentials` folder.

## Validation sequence

Run this sequence before trusting an unattended workflow run:

```powershell
pytest
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
python -m src.source_audit
python -m src.main --static-pages-smoke-test
python -m src.gmail_ingestion --run
python -m src.main --job-upsert-smoke-test
python -m src.dashboard
```

Expected results:

1. All tests pass.
2. Schema validation returns `ok: true`.
3. Static ingestion does not create generic search rows.
4. Gmail ingestion records every attempted message in `Gmail_Messages`.
5. LinkedIn digest cards retain their correct title, company, location, and canonical URL.
6. Dashboard and Digest write successfully without formula errors.

## Workbook schema

The required tabs are:

```text
Config_Searches
Config_Companies
Scoring_Rules
Target_Companies
Jobs
Job_Sources
Rejected_Jobs
Gmail_Messages
Snapshots
Runs
Digest
Dashboard
```

Validate the schema:

```powershell
python -m src.schema --validate
```

Repair canonical headers and the workbook timezone:

```powershell
python -m src.schema --repair-headers
```

Use repair when a required tab is missing, a header was edited, or the workbook timezone is not `America/Chicago`.

## Gmail ingestion

The production Gmail command is:

```powershell
python -m src.gmail_ingestion --run
```

The command lists every page of messages under the configured Gmail label, skips completed message IDs, and processes up to `GMAIL_MAX_RESULTS` pending messages.

Default configuration:

```text
GMAIL_LABEL_NAME=Job Tracker
GMAIL_MAX_RESULTS=50
```

The supported maximum is 500.

### Gmail message statuses

```text
success
no_jobs
retryable_failure
permanent_failure
```

A message is marked complete only after accepted jobs, rejected records, and the ledger status are written. Opening or reading an email does not affect ingestion.

`success`, `no_jobs`, and `permanent_failure` are skipped during normal runs. `retryable_failure` is retried.

### Rejected records

`Rejected_Jobs` is keyed by `rejected_id` for Gmail processing. Repeated runs update or skip existing rejection rows rather than appending duplicates.

Legitimate alert rejection does not fail a Gmail run. The Gmail step fails when every selected pending message fails to process.

### Controlled replay

Use only for debugging or deliberate replay:

```powershell
python -m src.gmail_ingestion --run --force-reprocess
```

### Backlog release

The production backlog is released only after Sprint 23 is merged and the validation sequence passes.

Run:

```powershell
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
python -m src.gmail_ingestion --run
python -m src.rescore_jobs
python -m src.dashboard
```

Confirm all labeled messages have ledger rows, backlog is zero, Topgolf and Toyota are correct, and both appear in `High-signal titles needing review`.

Run Gmail ingestion again. The expected result is zero newly processed messages and zero remaining backlog.

Detailed backlog instructions are in `docs/sprint_23_gmail_ingestion.md`.

## LinkedIn digest parsing

A valid LinkedIn card contains:

```text
Job title
Company
Location, when present
Direct LinkedIn posting URL
```

Accepted URLs are canonicalized to:

```text
https://www.linkedin.com/jobs/view/<job_id>
```

The source ID is:

```text
linkedin-<job_id>
```

Search pages, Premium links, alert management links, unsubscribe links, help pages, and LinkedIn navigation links do not create jobs.

Malformed cards are rejected individually. Valid cards in the same email remain eligible. LinkedIn alert confirmation emails remain quarantined and complete as no-job messages.

Regression fixtures:

```text
tests/fixtures/linkedin_topgolf.eml
tests/fixtures/linkedin_toyota.eml
```

## Sparse Gmail review routing

Sparse Gmail records with strategically relevant management titles receive:

```text
manual_review=true
review_reason=sparse_gmail_high_signal_title
```

The numerical score is not increased. Qualifying records appear in `High-signal titles needing review`.

Re-score existing open Gmail jobs with:

```powershell
python -m src.rescore_jobs
```

## Dashboard and Digest

Refresh both outputs manually with:

```powershell
python -m src.dashboard
```

The Dashboard should provide an executive answer, action queue, tracker health, source health, top roles, and source cleanup queue.

The weekly email is handled by `apps_script/weekly_digest_email.gs`. It is separate from the daily GitHub Actions workflow.

## GitHub Actions

The daily workflow is `.github/workflows/daily-run.yml`.

### Scheduled execution

Two UTC schedule entries remain for daylight-saving coverage. The workflow no longer depends on a 15-minute Central execution window.

Scheduled runs check `Runs` for a successful `daily_workflow_completion` record for the current Central date.

1. The first invocation runs when no successful completion exists.
2. A delayed first invocation still runs.
3. The second invocation skips after a successful first invocation.
4. The second invocation runs when the first invocation failed.
5. Manual dispatch always bypasses the lock.
6. Completion is recorded only after every required workflow step succeeds.

### Manual run

1. Open the repository in GitHub.
2. Select Actions.
3. Select `Job Tracker Daily Run`.
4. Choose `Run workflow` on `main`.
5. Leave `force_reprocess` off unless a controlled replay is required.
6. Review the Step Summary.

The summary reports the daily gate result, Gmail pages and messages, processing outcomes, backlog, accepted jobs, rejected alerts, and Dashboard and Digest row counts.

## Failure handling

### Schema validation failure

Repair headers before ingestion:

```powershell
python -m src.schema --repair-headers
python -m src.schema --validate
```

### Gmail retryable failures

Inspect `Gmail_Messages.error_message`, correct the cause, then rerun:

```powershell
python -m src.gmail_ingestion --run
```

### Gmail backlog remains

Review retryable rows and the GitHub Actions summary. A backlog warning means processing is incomplete, not that accepted jobs were lost.

### Rejected Gmail volume is high

Inspect recent email structure and `Rejected_Jobs`. Add a focused parser test before loosening quality rules.

### Dashboard refresh fails

Resolve the underlying error, then rerun:

```powershell
python -m src.dashboard
```

### Weekly email fails

Use `Send test weekly digest` from the Sheet menu, then inspect Apps Script executions and triggers.
