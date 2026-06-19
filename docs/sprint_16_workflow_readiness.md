# Sprint 16 workflow readiness

## Goal

Make GitHub Actions safe to run daily after Sprint 15 data quality gates are in place.

Sprint 16 updates the daily workflow so it validates the repo, workbook, credentials, ingestion outputs, and review outputs in the correct order.

## Implemented workflow order

The daily workflow is `.github/workflows/daily-run.yml`.

Current order:

1. Check Central schedule window.
2. Checkout.
3. Set up Python.
4. Install dependencies.
5. Run tests.
6. Validate required secrets.
7. Write temporary credential files.
8. Validate workbook schema.
9. Record workflow validation in `Runs`.
10. Run static career pages.
11. Run Gmail ingestion if optional Gmail secrets exist.
12. Skip Gmail cleanly if optional Gmail secrets are missing.
13. Run Greenhouse, Lever, and lifecycle handling.
14. Refresh Dashboard and Digest.
15. Write GitHub Step Summary.

## Safety behavior

The workflow stops when required preconditions fail.

Hard fail conditions:

1. Tests fail.
2. Required Google Sheets secrets are missing.
3. Workbook schema validation fails.
4. Static page rejected record count reaches the failure threshold.
5. Dashboard or Digest refresh fails.

Warning conditions:

1. Static page rejected record count reaches the warning threshold.
2. Gmail rejected alert rows exceed accepted job rows.

Clean skip condition:

1. Gmail ingestion skips when `GMAIL_CLIENT_CONFIG` or `GMAIL_TOKEN_JSON` is missing.

## Workflow metrics

The GitHub Step Summary writes these metrics:

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

## Required secrets

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON
```

## Optional Gmail secrets

```text
GMAIL_CLIENT_CONFIG
GMAIL_TOKEN_JSON
```

The workflow writes secret JSON values to temporary files under the GitHub runner temp directory. Credential contents should not be printed.

## Main files

```text
.github/workflows/daily-run.yml
src/workflow_validation.py
src/schema.py
src/dashboard.py
```

## Runs records

Sprint 16 adds a workflow validation record using `src/workflow_validation.py`.

The run record uses:

```text
run_type=sprint_16_workflow_validation
source_type=workflow
source_name=Daily run schema preflight
```

Dashboard refresh also appends a run record from `src/dashboard.py`.

## Validation commands

Run locally before relying on the GitHub workflow:

```powershell
pytest
python -m src.schema --validate
python -m src.main --static-pages-smoke-test
python -m src.main --gmail-alerts-smoke-test
python -m src.main --job-upsert-smoke-test
python -m src.dashboard
```

Then run the workflow manually in GitHub Actions and inspect the Step Summary.

## Notes

The workflow should not be used to paper over bad source configuration. If static page rejected counts are high, Sprint 18 should correct or disable low-value sources rather than lowering the gate.

If Dashboard refresh fails, the workflow should fail because the review output is incomplete.
