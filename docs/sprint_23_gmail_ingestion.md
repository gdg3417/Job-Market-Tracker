# Sprint 23 Gmail ingestion reliability

## Commands

```powershell
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --validate
python -m src.gmail_ingestion --run
```

The canonical workbook includes `Gmail_Messages`. It records message identity, status, attempt counts, processing counts, errors, and timestamps.

Supported statuses are `success`, `no_jobs`, `retryable_failure`, and `permanent_failure`. Completed messages are skipped on normal runs. Retryable messages remain eligible. Gmail read status is not used.

`GMAIL_MAX_RESULTS` controls how many pending messages are fully processed. The default is 50 and the maximum is 500. Gmail listing follows page tokens. Jobs and Job_Sources are loaded once per process, and rejected records are idempotent by `rejected_id`.

The legacy command delegates to the same runner:

```powershell
python -m src.main --gmail-alerts-smoke-test
```

## Backlog release after merge

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

Verify all labeled messages have ledger rows, `backlog_remaining` is zero, confirmation emails are `no_jobs`, valid digests are `success`, and `Rejected_Jobs` has no duplicate `rejected_id` values.

Topgolf must be stored as `Sr Manager, Strategic Planning`, `Topgolf`, `Dallas, TX`, LinkedIn job ID `4417965465`. Toyota must be stored as `National Manager, Product`, `Toyota North America`, `Plano, TX`, LinkedIn job ID `4430066274`. Both must use canonical LinkedIn URLs and appear under `High-signal titles needing review`.

Run ingestion a second time. Expected results are `status: no_new_messages`, `new_messages_processed: 0`, `failed_messages: 0`, and `backlog_remaining: 0`.

## Scheduling and retries

The workflow retains two UTC schedules and uses `Runs` as a Central-date completion lock. Scheduled starts before 06:30 AM Central are skipped. Manual dispatch bypasses the lock.

The first eligible invocation runs when no successful completion exists. A successful run records completion and the second invocation skips. A partial Gmail result keeps successful message records, allows later workflow steps to run, and does not record daily completion. The later invocation can therefore retry only unsuccessful messages. The Gmail step fails when every selected message fails or the result cannot be validated.

## Controlled replay

```powershell
python -m src.gmail_ingestion --run --force-reprocess
```

Use replay only for deliberate debugging. Dedupe and rejection idempotency remain active.

## Recovery

Review `Gmail_Messages.error_message`, correct the underlying problem, and rerun normal ingestion. For missing or incorrect ledger headers, use:

```powershell
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --repair-headers
python -m src.schema --validate
```
