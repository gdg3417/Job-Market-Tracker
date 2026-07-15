# Troubleshooting and Recovery

Use the smallest safe recovery scope. Preserve audit evidence and confirm whether canonical writes occurred before replaying work.

## Gmail backlog or failed messages

1. Open the failed `Job Tracker Daily Run` and read the first failing step.
2. Review the Step Summary for messages fetched, processed, failed, quarantined, backlog remaining, systemic category, and completion-record status.
3. Review `Gmail_Messages` for current state and `Gmail_Failures` for message-level diagnostics.
4. Correct the shared failure before replaying when every message failed at the same stage.
5. Rerun in `failed_only` mode for retryable failed or pending messages.
6. Use `selected` mode only with exact message IDs.
7. Force selected replay only after confirming a completed message must be deliberately reprocessed.
8. Confirm no duplicate `Jobs`, `Job_Sources`, `Rejected_Jobs`, or Gmail ledger rows were created.
9. Confirm the successful daily completion record is written only after retryable failures are cleared.

A quarantined malformed message should remain visible with its reason. Do not delete it to clear the backlog.

## Gmail credentials or authentication

1. Confirm the GitHub secrets `GMAIL_CLIENT_CONFIG` and `GMAIL_TOKEN_JSON` exist and contain valid JSON.
2. Confirm the workflow credential step reports Gmail ready.
3. Distinguish invalid or expired credentials from Gmail API quota or rate-limit errors.
4. Refresh credentials outside the repository and replace the secrets.
5. Do not commit credential files, token contents, or service-account JSON.
6. Rerun the daily workflow in normal mode.

## Google Sheets quota exhaustion

1. Confirm the original exception is a quota or rate-limit response rather than a schema or capacity error.
2. Do not immediately start multiple workbook workflows. They already share one serialized concurrency group.
3. Let the queued workflow complete or cancel only when a run is known to be unrecoverable.
4. Wait for quota recovery, then rerun the smallest affected workflow.
5. Use `Surface_Status`, `Runs`, and workflow summaries to identify which writes completed.
6. Do not replay ingestion solely because a later Dashboard or summary write failed.

Verification health includes a quota cooldown after schema preflight. Repeated quota failures can indicate excessive manual reruns or a broader workbook read pattern that requires a code patch.

## Workbook-capacity warning or critical result

1. Run `Job Tracker Workbook Capacity` with both approvals disabled.
2. Review allocated cells, reclaimable cells, structural ranges, and unknown metadata.
3. Repair any displaced values or formulas outside expected ranges before compaction.
4. Create a current workbook backup.
5. Enable compaction only after the audit is clean.
6. Enable blank-formatting trim only when formatting-only trailing grid cells are intentionally approved.
7. Confirm capacity falls below 80 percent.
8. Rerun compaction and confirm zero additional requests.
9. Run `Job Tracker Sheet UX Governance` after compaction.
10. Verify filters, freezes, dropdowns, formatting, and manual `Jobs` data.

Never delete rows or columns solely because they look blank. Notes, validation, formulas, formatting, named ranges, filters, and conditional formats can be preservation boundaries.

## Verification-health failure

1. Confirm schema validation completed before the calculation failed.
2. Read the original traceback and failure detail, not only the final workflow status.
3. Determine whether the cause is quota, blank or malformed records, missing identity, or a calculation defect.
4. Run `dry-run` after a code or data correction when diagnosis is needed without workbook writes.
5. Run `run` to refresh the Dashboard verification section and `Runs` history.
6. Confirm actionable health and portfolio coverage are both present.
7. Confirm no displayed conversion exceeds 100 percent.
8. Confirm dismissed, terminal, and not-yet-due deferred roles do not inflate actionable debt.

A verification-health failure does not invalidate previously written canonical jobs. It means current operational health reporting is stale or incomplete.

## Stale or partially refreshed generated surfaces

1. Review `Surface_Status` for the failed or stale surface.
2. Confirm canonical `Jobs` data is correct before refreshing presentation outputs.
3. Run `Job Tracker Weekly Value Refresh` or:

```powershell
python -m src.presentation_refresh --refresh --source-run "manual-recovery"
```

4. Review per-surface success, warning, row count, and data as-of date.
5. Rerun with governance only when filters, freezes, header colors, or dropdowns also need repair.
6. Do not edit the stale generated worksheet directly.

The refresh continues after isolated surface failures and reports partial completion. Correct the failed generator before treating the presentation layer as current.

## Static source failure

1. Review `Source_Audit` for classification, retry date, and required action.
2. Confirm the match uses the exact company ID and normalized configured source URL.
3. Treat one 403, DNS failure, timeout, or server failure as recoverable.
4. Do not classify a source as permanently retired from one 404.
5. Prefer a validated structured ATS destination over fragile landing-page scraping.
6. Run `Job Tracker Source Quality` in report mode with live probes.
7. Use reviewed cleanup only with exact approved company IDs and exact current URLs.
8. Confirm strategic target-company coverage remains available.

No-probe mode can refresh yield reporting, but it preserves the last live `Source_Audit` and cannot apply cleanup.

## Schema mismatch or edited headers

1. Stop workflows that would write the affected worksheet.
2. Compare the workbook headers to `src/schema.py`.
3. Use migration when canonical trailing fields are missing:

```powershell
python -m src.schema --migrate
python -m src.schema --validate
```

4. Use repair only when required tabs, header order, or workbook timezone are incorrect:

```powershell
python -m src.schema --repair-headers
python -m src.schema --validate
```

5. Confirm `Jobs` field order and manual data are preserved.
6. Rerun the failed production workflow.

Do not manually reorder canonical columns to match a visual preference.

## Enrichment failure or stuck queue work

1. Review `Enrichment_Queue.error_type`, `error_message`, attempt count, and next retry time.
2. Run a normal production cycle to recover stale `in_progress` rows older than the configured threshold.
3. Use one exact job key for isolated recovery:

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

4. Use weekly mode when external-search fallback or broader lifecycle work is needed.
5. Confirm accepted evidence before changing canonical fields manually.
6. Do not close a role from one timeout, HTTP 429, HTTP 5xx response, blocked page, parser failure, or untrusted result.

## Authoritative posting resolution problem

1. Review `Posting_Resolution` and all matching `Resolution_Candidates` rows.
2. Confirm company, title, location, requisition, and ATS evidence.
3. Treat `retryable_failure`, `blocked`, and `ambiguous` as unresolved states, not closure evidence.
4. Use a validated manual authoritative URL only through the approved manual fields.
5. Rerun the exact job after correction.
6. Preserve losing candidates and prior evidence for auditability.

## Duplicate or replay concern

1. Compare `job_key`, source job ID, canonical URL, company, title, and location.
2. Review `Job_Sources` before deleting or merging anything manually.
3. Confirm whether the workflow failed before or after canonical upsert.
4. Prefer a normal rerun, because writes are designed to be idempotent.
5. Use force replay only for exact selected Gmail messages.
6. Do not delete source or Gmail ledger rows to make a replay possible.
7. Add a focused regression test before changing deduplication rules.

## Weekly Context email failure

1. Confirm `Weekly_Context` refreshed successfully and is not stale in `Surface_Status`.
2. Confirm the Google Apps Script trigger still exists.
3. Use `Send test weekly digest` from the Sheet menu.
4. Review Apps Script executions for authorization, quota, recipient, or template errors.
5. Confirm the fallback digest is available when `Weekly_Context` is missing or empty.
6. Rerun the GitHub weekly refresh only when the workbook contract is stale. GitHub Actions does not send the Apps Script email directly.

## Pull request checks fail

1. For `Pull Request Tests`, inspect compilation and the first failing pytest case.
2. For `Regression readiness`, inspect both pytest and the gold-standard evaluation.
3. Do not update the regression fixture merely to make a failure disappear.
4. Rebase or merge current `main` into the branch when the failure comes from stale branch state.
5. Confirm both exact checks pass before squash merge.

## Workflow fails before summary output

The likely causes are invalid secrets, dependency installation, YAML or shell errors, schema failure, or an unhandled exception before structured output was written.

Read the first failed step and original stderr. The summary is a secondary diagnostic surface and may contain only a fallback error when the primary command never started.
