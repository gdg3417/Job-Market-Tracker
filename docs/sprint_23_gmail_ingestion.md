# Sprint 23 Gmail ingestion reliability

## Production command

```powershell
python -m src.gmail_ingestion --run
```

The canonical workbook now includes `Gmail_Messages` with message identity, status, attempt counts, processing counts, error text, and processing timestamps.

Supported statuses are `success`, `no_jobs`, `retryable_failure`, and `permanent_failure`. Successful and no-job messages are skipped on later runs. Retryable failures remain eligible. Gmail read or unread state is not used.

Gmail listing follows page tokens. `GMAIL_MAX_RESULTS` controls the number of pending messages fully processed per run. The default is 50 and the supported maximum is 500.

Rejected records use `rejected_id` as the idempotency key, so retries do not append the same rejected row again.

## Preflight

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
python -m src.source_audit
```

Do not release the production backlog until all preflight checks pass.

## Controlled backlog release

After Sprint 23 is merged to `main`:

```powershell
git checkout main
git pull --ff-only origin main
.\.venv\Scripts\Activate.ps1
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
python -m src.gmail_ingestion --run
python -m src.rescore_jobs
python -m src.dashboard
```

Confirm the following:

1. Every currently labeled Gmail message has a ledger row.
2. `backlog_remaining` is zero.
3. Alert confirmation emails have a completed no-job status.
4. Valid digest emails have `success` status.
5. Topgolf is stored as `Sr Manager, Strategic Planning`, `Topgolf`, `Dallas, TX`, LinkedIn job ID `4417965465`.
6. Toyota is stored as `National Manager, Product`, `Toyota North America`, `Plano, TX`, LinkedIn job ID `4430066274`.
7. Both roles use canonical LinkedIn posting URLs.
8. Both roles appear in `High-signal titles needing review`.
9. `Rejected_Jobs` has no duplicate `rejected_id` values.

Run the command again:

```powershell
python -m src.gmail_ingestion --run
```

Expected second-run values are `new_messages_processed: 0`, `failed_messages: 0`, `backlog_remaining: 0`, and `status: no_new_messages`.

## Controlled replay

```powershell
python -m src.gmail_ingestion --run --force-reprocess
```

Use replay only for debugging or deliberate reprocessing. Job dedupe and rejected-record idempotency remain active.

## Daily workflow lock

The workflow retains two UTC schedules. Scheduled execution checks `Runs` for a successful `daily_workflow_completion` record for the current Central date.

The first invocation runs when no completion exists. The second skips after a successful first run, but retries when the first run failed. Manual workflow dispatch always runs. Completion is recorded only after all required workflow steps succeed.

## Recovery

For retryable failures, inspect `Gmail_Messages.error_message`, correct the cause, and rerun the normal ingestion command.

When `Gmail_Messages` is missing, run:

```powershell
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
```

When canonical headers are incorrect, run:

```powershell
python -m src.schema --repair-headers
python -m src.schema --validate
```
