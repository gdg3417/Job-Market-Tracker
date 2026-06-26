# Production Runbook

## Scheduled operation

The normal sequence is:

1. `Job Tracker Daily Run` ingests static and Gmail leads.
2. A successful daily workflow triggers `Job Tracker Enrichment Run` in `daily` mode.
3. Sunday enrichment runs in `weekly` mode for external discovery and lifecycle checks.
4. The bound Google Apps Script sends the weekly email on its separate schedule.

## Manual validation

```powershell
python -m src.schema --migrate
python -m src.schema --validate
python -m src.enrichment.production --dry-run --mode daily
pytest
```

## Manual daily cycle

```powershell
python -m src.enrichment.production --run --mode daily
```

## Manual weekly cycle

```powershell
python -m src.enrichment.production --run --mode weekly
```

## Controlled single-job cycle

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

## Inspecting a result

Review these worksheets:

* `Jobs`
* `Job_Sources`
* `Enrichment_Queue`
* `Enrichment_Evidence`
* `Posting_Resolution`
* `Resolution_Candidates`
* `Runs`
* `Dashboard`
* `Digest`

For a confident merge, confirm the selected resolution is `resolved_authoritative` or `manual_override`, the accepted evidence URL is authoritative, confidence meets the configured threshold, and the evidence title, company, location, and requisition identifiers describe the tracked role.

## Recovery from a failed workflow

1. Read the GitHub Step Summary.
2. Confirm whether the failure was system-level or limited to individual jobs.
3. Correct credentials or schema errors before rerunning.
4. Leave per-job failures in the queue for the next scheduled retry.
5. Rerun the same mode manually when the system-level issue is fixed.

Stale `in_progress` rows older than 90 minutes are recovered automatically on the next production cycle.

## Cost control

The daily cycle performs bounded authoritative resolution without external search, followed by the existing enrichment stages. The weekly and backfill modes allow controlled resolver search fallback. The daily cycle does not perform external search. The weekly and backfill defaults allow five external-search jobs. Search results are cached in evidence, and only authoritative candidate pages are fetched for scoring.

No paid search provider is required.
