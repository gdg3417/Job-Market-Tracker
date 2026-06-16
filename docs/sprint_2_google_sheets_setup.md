# Sprint 2: Google Sheets setup

Sprint 2 connects the local Python project to the Job Market Tracker Google Sheet.

## Acceptance criteria

The Sprint 2 smoke test is complete when Python can:

1. Read `Config_Companies`.
2. Read `Config_Searches`.
3. Append one test row to `Runs`.
4. Keep credentials out of GitHub.
5. Use a service account with access only to the tracker Sheet.

## Google Cloud setup

1. Create or open a Google Cloud project.
2. Enable the Google Sheets API.
3. Create a service account.
4. Create a JSON key for the service account.
5. Download the JSON key to your computer.
6. Put the JSON file here locally:

```text
credentials/google-credentials.json
```

The `credentials/` folder is ignored by Git and should not be committed.

## Google Sheet sharing

1. Open the Google Sheet named `Job Market Tracker`.
2. Open the downloaded service account JSON.
3. Copy the `client_email` value.
4. Share the Google Sheet with that email.
5. Use Editor access for now, because the Sprint 2 smoke test writes to `Runs`.

Do not share broader Google Drive folders with the service account. Share only this tracker Sheet.

## Local `.env` setup

Copy `.env.example` to `.env`.

```powershell
copy .env.example .env
```

Update these values in `.env`:

```text
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_APPLICATION_CREDENTIALS=credentials/google-credentials.json
```

The Sheet ID is the long value in the Google Sheet URL between `/d/` and `/edit`.

You can leave this setting as true:

```text
JOB_TRACKER_DRY_RUN=true
```

The explicit `--sheets-smoke-test` flag bypasses the dry-run default.

## Run the Sprint 2 smoke test

From the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main --sheets-smoke-test
```

Expected output:

```json
{
  "run_mode": "sprint_2_sheets_smoke_test",
  "status": "success",
  "config_companies_rows": 25,
  "config_searches_rows": 8,
  "runs_row_appended": true,
  "run_id": "sprint2_sheets_smoke_..."
}
```

The row counts will reflect your actual tracker Sheet.

## Troubleshooting

### `GOOGLE_SHEET_ID is required`

Add the Sheet ID to `.env`.

### `GOOGLE_APPLICATION_CREDENTIALS is required`

Add the credential file path to `.env`.

### `Google credentials file was not found`

Confirm the JSON file exists at the path in `.env`.

### `The caller does not have permission`

Share the Google Sheet with the service account `client_email` from the JSON file.

### `Worksheet Config_Companies not found`

Confirm the workbook tabs from Sprint 0 exist and use the exact tab names.

### `No headers in worksheet Runs matched the record keys`

Confirm the `Runs` tab has a header row. At minimum, include one of these headers: `run_id`, `status`, `started_at`, `finished_at`, `records_found`, `error_message`, or `notes`.
