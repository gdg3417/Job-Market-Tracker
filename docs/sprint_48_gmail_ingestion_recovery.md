# Sprint 48: Gmail Ingestion Recovery and Diagnostics

## Current-state reassessment

Sprint 48 began by reassessing the live workbook and recent workflow history rather than assuming the original incident was still active.

Findings:

1. The live `Gmail_Messages` ledger has no current `retryable_failure` or `permanent_failure` rows.
2. Gmail ingestion processed messages successfully on July 14, 2026, and the prior backlog is cleared.
3. The backup workbook created before the July 14 recovery preserved 19 failed message rows.
4. All 19 messages were retrieved and parsed successfully.
5. Every failure occurred during the job workbook-write stage with the same Google Sheets error: the write would increase the workbook above the 10,000,000 cell limit.
6. The incident was therefore a systemic workbook-capacity failure, not an authentication, Gmail API, parser, or deduplication failure.
7. Successful recovery overwrote the prior `error_message` values in `Gmail_Messages`, which demonstrated the need for a separate immutable failure-attempt audit.

Sprint 47 resolved the workbook-capacity condition. Sprint 48 does not repeat capacity compaction work.

## Implementation

### Failure diagnostics

A system-managed `Gmail_Failures` worksheet records each failed attempt separately. It stores:

* Failure identifier
* Gmail message and thread identifiers
* Subject and sender
* Received timestamp
* Attempt count
* Failure stage
* Normalized error category
* Sanitized concise error message
* Retry eligibility
* Systemic-failure flag
* Stable failure fingerprint
* First failure and last attempt timestamps
* Final attempt status

Email body contents and authentication secrets are not written to the diagnostic worksheet or GitHub Step Summary.

Supported normalized categories are:

* `authentication`
* `gmail_api`
* `parsing`
* `deduplication`
* `workbook_write`
* `configuration`
* `unknown`

### Systemic failure detection

When every selected message fails with the same category, stage, and fingerprint, the run is classified as systemic. Systemic failures remain retryable even when individual attempt counts exceed the isolated-message limit. This prevents a shared infrastructure failure from quarantining an entire backlog.

### Bounded retry and quarantine

Isolated retryable message failures are attempted across runs up to the configured maximum, which defaults to three attempts. After the limit, the message is marked `quarantined` and excluded from normal automatic replay.

Non-retryable isolated parsing failures are quarantined immediately with a precise diagnostic record.

### Safe replay controls

The daily workflow supports three Gmail replay modes:

1. `normal`: process new and retryable messages while skipping completed and quarantined messages.
2. `failed_only`: process only messages currently marked `retryable_failure`.
3. `selected`: process only exact Gmail message IDs supplied in the workflow input.

Completed messages can be reprocessed only when exact message IDs are supplied and `force_reprocess_selected` is enabled. Broad force replay is no longer exposed by the workflow.

Equivalent command-line examples:

```bash
python -m src.gmail_ingestion --run
python -m src.gmail_ingestion --run --retry-failed-only
python -m src.gmail_ingestion --run --message-id MESSAGE_ID
python -m src.gmail_ingestion --run --message-id MESSAGE_ID --force-reprocess-selected
python -m src.gmail_ingestion --run --retry-failed-only --max-message-attempts 3
```

All accepted job writes continue through the existing idempotent `Jobs` and `Job_Sources` upsert paths. Replaying a message must not create duplicate canonical rows.

### Authentication behavior

GitHub Actions ingestion is non-interactive. Invalid or expired credentials that cannot be refreshed produce a normalized `authentication` failure instead of attempting to launch a local OAuth browser flow.

### Completion records

Each Gmail ingestion invocation attempts to append a `gmail_ingestion_reliability` run record, including all-message failure and systemic setup failure cases after workbook connection succeeds.

The daily workflow also appends a `daily_workflow_attempt` record on every attempted run. This record captures successful, incomplete, failed, or cancelled execution without satisfying the daily success lock.

The existing `daily_workflow_completion` record remains success-only. It is withheld while retryable Gmail failures remain so a later scheduled invocation can recover the backlog.

## GitHub Step Summary

The daily summary reports:

* Ingestion status and replay mode
* Messages fetched
* Messages already processed
* Messages successfully processed
* Processing failures
* Retryable failures
* Quarantined messages
* Remaining backlog
* Systemic failure category and stage
* Whether the Gmail run record was written
* Whether message diagnostics were written
* Whether the daily attempt record was written
* Whether the successful daily completion record was written

## Regression coverage

Tests cover:

* Successful batch processing
* One malformed message
* Shared workbook failure across every selected message
* Authentication failure
* Bounded retry and quarantine
* Failed-only backlog replay
* Exact selected replay
* Rejection of broad force replay
* Replay idempotence for `Jobs` and `Job_Sources`
* Gmail run record creation during all-message failure
* Daily attempt records that do not satisfy the successful completion lock

## Post-merge validation

1. Run `Job Tracker Daily Run` with replay mode `normal`.
2. Confirm the Gmail Step Summary reports zero retryable failures and zero backlog.
3. Confirm `Gmail_Failures` exists with the documented headers. It may contain no rows when no new failures occur.
4. Confirm a `gmail_ingestion_reliability` row is added to `Runs`.
5. Confirm a `daily_workflow_attempt` row is added to `Runs`.
6. Confirm a `daily_workflow_completion` row is added only when the full daily run succeeds.
7. Confirm no duplicate `Jobs`, `Job_Sources`, or `Gmail_Messages` rows are created.
