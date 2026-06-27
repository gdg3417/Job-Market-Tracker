# Troubleshooting

## Queue row remains `in_progress`

Run a normal production cycle. Rows older than 90 minutes are recovered automatically.

To use a shorter threshold during controlled testing:

```powershell
python -m src.enrichment.production --run --mode daily --stale-after-minutes 5
```

## Direct URL fails

Check `error_type` and `error_message` in `Enrichment_Queue`.

A blocked, missing, or non-job direct page should hand off to company or ATS discovery. A transient network failure should remain retryable.

## Company or ATS configuration is missing

Review `Config_Companies` for:

* canonical company name
* aliases
* career domain
* career search URL
* ATS platform
* board or company identifier
* active enrichment flag

Do not enable a guessed ATS identifier.

## External search finds the wrong role

Leave the candidate evidence rejected. Search discovery is not scoring evidence. Correct the company alias, title normalization, location, or authoritative URL, then rerun one exact job.

## Job is incorrectly marked closed

Review `lifecycle_evidence_type`, `lifecycle_evidence_url`, `lifecycle_reason`, and the accepted evidence row.

One timeout, HTTP 429, HTTP 5xx response, parser failure, or untrusted page cannot close a role. Confirm that closure came from an explicit authoritative statement, expired `validThrough`, or repeated authoritative absence on different dates.

## Duplicate-looking jobs

Compare `job_key`, `source_job_id`, canonical URL, company, title, and location. Enrichment updates an existing job and should not create a new job identity.

Do not delete a row until `Job_Sources` and posting IDs have been reviewed.

## Workflow fails before summary output

The likely causes are invalid secrets, schema failure, dependency failure, or an unhandled workbook error. Individual job retrieval failures should not terminate the workflow.

## Authoritative posting resolution

Preview one job before writing:

```powershell
python -m src.resolution.run --dry-run --limit 1 --job-key "<job_key>"
```

Common states:

* `resolved_probable`: inspect visible score components. Confirm company, title, location, and requisition evidence before using a manual override.
* `ambiguous`: compare all rows for the job in `Resolution_Candidates`. Do not delete the losing candidates.
* `blocked`: confirm the URL is an employer or ATS posting rather than a job-board page. A block does not mean the job is closed.
* `unsupported`: verify `Config_Companies` has the correct career domain, career search URL, ATS platform, and ATS identifier. Structured connector expansion belongs in Sprint 35.
* `retryable_failure`: rerun later. Do not replace the URL solely because of one timeout, rate limit, or server failure.

For a validated manual URL, enter the manual fields in `Posting_Resolution` and rerun the exact job. Use `remove` to clear an override while preserving an audit row.
