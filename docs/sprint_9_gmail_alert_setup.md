# Sprint 9 Gmail alert ingestion setup

Sprint 9 ingests job alert emails from Gmail without scraping LinkedIn, Indeed, Google Jobs, or job boards directly.

## Manual Gmail setup

1. Create a Gmail label named `Job Tracker`.
2. Create Gmail filters that apply `Job Tracker` to job alert emails.
3. Start with these filter patterns:

```text
from:(jobalerts-noreply@linkedin.com OR alerts@indeed.com OR googlealerts-noreply@google.com)
```

Add company alert senders and recruiter distribution lists as you find useful sources.

## Local Gmail API setup

Create an OAuth desktop client in Google Cloud with the Gmail API enabled. Download the OAuth client JSON into the ignored `credentials/` folder.

Add these values to `.env`:

```text
GMAIL_CLIENT_CONFIG=credentials/gmail-client-config.json
GMAIL_TOKEN_JSON=credentials/gmail-token.json
GMAIL_LABEL_NAME=Job Tracker
GMAIL_MAX_RESULTS=50
```

Run the first authorization locally:

```powershell
python -m src.main --gmail-alerts-smoke-test
```

The first run opens a local browser authorization flow and writes `GMAIL_TOKEN_JSON`. Later runs reuse or refresh that token.

## What the command does

`python -m src.main --gmail-alerts-smoke-test` performs these steps:

1. Reads messages with the configured Gmail label.
2. Extracts job title, company, location, URL, source job ID, and received date.
3. Marks source as `gmail_alert`.
4. Normalizes and scores extracted jobs.
5. Upserts jobs into `Jobs` and `Job_Sources`.
6. Appends a Sprint 9 summary row to `Runs`.
7. Flags uncertain extractions by setting low confidence and adding `manual_review_required` to `description_text`.

## Recommended filters

Use Gmail filters to capture only alert style messages. Do not label every recruiter email at first because low structure emails create more manual review rows.

Good starting filters:

```text
from:jobalerts-noreply@linkedin.com
from:alerts@indeed.com
from:googlealerts-noreply@google.com
subject:(job alert)
subject:(new jobs)
```

Add company specific alert senders after the core flow works.

## Notes for GitHub Actions

Gmail ingestion requires `GMAIL_CLIENT_CONFIG` and `GMAIL_TOKEN_JSON`. For GitHub Actions, store those as secrets and write them to temporary files during the workflow. Sprint 12 handles the scheduled automation layer.
