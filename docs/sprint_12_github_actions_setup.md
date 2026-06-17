# Sprint 12: GitHub Actions daily automation setup

Sprint 12 runs the tracker automatically from GitHub Actions and keeps credentials out of the repository.

## Workflow file

```text
.github/workflows/daily-run.yml
```

The workflow supports:

1. Manual runs through `workflow_dispatch`.
2. Scheduled daily runs at 06:30 AM Central.
3. Python 3.11 setup.
4. Dependency installation from `requirements.txt`.
5. Unit tests through `pytest`.
6. Temporary Google credential file creation from GitHub Secrets.
7. Static career page ingestion.
8. Optional Gmail alert ingestion.
9. Greenhouse and Lever ingestion with lifecycle tracking last.
10. A GitHub Step Summary after each run.

## Required GitHub Secrets

Add these secrets under the repository settings.

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON
```

`GOOGLE_SHEET_ID` should be the spreadsheet ID from the Job Market Tracker Google Sheet URL.

`GOOGLE_SERVICE_ACCOUNT_JSON` should be the full JSON contents of the Google Sheets service account credential file. Do not paste only the file path. GitHub Actions cannot access your local machine.

## Optional GitHub Secrets for Gmail ingestion

Only add these if Gmail alert ingestion should run in GitHub Actions.

```text
GMAIL_CLIENT_CONFIG
GMAIL_TOKEN_JSON
```

`GMAIL_CLIENT_CONFIG` should be the full JSON contents of the OAuth client config file.

`GMAIL_TOKEN_JSON` should be the full JSON contents of the Gmail OAuth token file.

If either optional Gmail secret is missing, the workflow skips Gmail ingestion and still runs the other tracker steps.

## How to add the secrets

1. Open the repository on GitHub.
2. Go to Settings.
3. Go to Secrets and variables.
4. Choose Actions.
5. Add each secret under Repository secrets.
6. Paste the secret value directly into GitHub.
7. Save each secret.

Do not add these values to `.env`, the README, issue comments, workflow logs, or committed files.

## Schedule behavior

GitHub cron schedules run in UTC. The workflow includes two UTC cron entries and a local Central-time gate.

```yaml
schedule:
  - cron: "30 11 * * *"
  - cron: "30 12 * * *"
```

The workflow only continues when the current `America/Chicago` time is within the 06:30 AM to 06:45 AM window. This handles the difference between Central Daylight Time and Central Standard Time.

Manual runs do not use the schedule gate.

## Run order

The workflow runs source groups in this order:

1. Static career pages.
2. Gmail alert ingestion, only when Gmail secrets are configured.
3. Greenhouse, Lever, and lifecycle tracking.

Lifecycle runs last so jobs already seen from static pages or Gmail during the same day are less likely to be incorrectly aged as missing.

## Failure behavior

The workflow attempts each source group even if an earlier source group exits with an error.

Source-level failures that are handled inside Python should write to the `Runs` tab and not fail the whole workflow. Unhandled command failures are surfaced as GitHub Actions errors after the remaining source groups have been attempted.

## Manual test steps

After adding the required secrets, run the workflow manually.

1. Open the repository on GitHub.
2. Go to Actions.
3. Choose `Job Tracker Daily Run`.
4. Choose `Run workflow`.
5. Use the `main` branch.
6. Review the workflow logs.
7. Confirm the `Runs` tab received new rows.
8. Confirm `Jobs` and `Job_Sources` updated where postings were found.

## Expected output

A successful run should show:

1. Tests pass.
2. Required secrets validate without printing their values.
3. Temporary credential files are created under the GitHub runner temp folder.
4. Static career pages run.
5. Gmail is either run or skipped based on optional secrets.
6. Greenhouse, Lever, and lifecycle run.
7. A GitHub Step Summary is created.
8. The `Runs` tab has source-level and summary records.

## Troubleshooting

If the workflow fails before running Python, check that both required secrets exist and contain values.

If Google Sheets access fails, confirm the service account email has access to the Job Market Tracker Sheet.

If Gmail ingestion fails, run it locally first with the same token files. Gmail requires OAuth token access, not the Google Sheets service account.

If a connector fails but the workflow continues, review the `Runs` tab for the source name, status, and error message.

If the workflow appears to skip a scheduled run, check the schedule gate log. Scheduled events can trigger twice per day in UTC, but only the Central 06:30 AM window should continue.