# Sprint 32: Production Hardening and Controlled Rollout

## Goal

Integrate enrichment into production without allowing one failed job, one interrupted workflow, or an unbounded backfill to destabilize the daily tracker.

Topgolf `Sr Manager, Strategic Planning` and Toyota North America `National Manager, Product` remain permanent regression cases.

## Production runner

The production entrypoint is:

```powershell
python -m src.enrichment.production --run --mode daily
```

Supported modes:

| Mode | Direct limit | Company or ATS limit | External-search limit | Lifecycle limit |
| --- | ---: | ---: | ---: | ---: |
| `daily` | 10 | 10 | 0 | 0 |
| `weekly` | 10 | 10 | 5 | 50 |
| `backfill` | 15 | 15 | 5 | 50 |

Each cycle performs these steps:

1. Recover stale queue rows left as `in_progress`.
2. Run direct-link enrichment.
3. Run configured company and ATS enrichment.
4. Run external search only when the selected mode permits it.
5. Run lifecycle checks only when the selected mode permits it.
6. Re-score selected open jobs.
7. Refresh Dashboard and Digest.
8. Append enrichment and lifecycle health metrics to Dashboard.
9. Record one production run in `Runs`.

Per-job failures remain recorded in `Enrichment_Queue` and `Enrichment_Evidence`. They do not fail the workflow. System-level failures, such as invalid credentials, invalid schema, or an unhandled workbook error, fail the workflow.

## Interrupted-run recovery

Every runner marks a queue item `in_progress` before network work begins. A cancelled runner can therefore leave a stale row behind.

Sprint 32 checks for `in_progress` rows older than 90 minutes by default.

Recovery preserves the correct stage handoff:

| Interrupted stage | Recovery state |
| --- | --- |
| `direct_url` | `direct_url` plus `retryable_failure` |
| `company_ats` | `direct_url` plus `not_found`, which makes company or ATS processing eligible again |
| `external_search` | `company_ats` plus `not_found`, which makes external search eligible again |

The prior attempt count is retained. Recovery does not silently grant a fresh attempt budget.

## GitHub Actions

`.github/workflows/enrichment-run.yml` has three entry paths:

1. A successful `Job Tracker Daily Run` on `main` triggers the `daily` production cycle.
2. A Sunday schedule runs the `weekly` production cycle.
3. Manual dispatch supports `daily`, `weekly`, or `backfill`, with an optional exact `job_key`.

One concurrency group prevents overlapping enrichment workflows. The job timeout is 45 minutes.

The daily cycle performs only direct-link and company or ATS work. External discovery and lifecycle checks remain in the weekly cycle.

## Workflow summary

The GitHub Step Summary reports:

* jobs evaluated for potential priority
* jobs enqueued
* queue backlog
* stale `in_progress` rows recovered
* direct-link attempts
* ATS attempts
* external searches
* successful and partial enrichments
* ambiguous matches
* retryable and permanent failures
* verified scores created
* likely closed and closed jobs
* Dashboard and Digest rows written

## Controlled rollout

### Phase 1: Regression jobs

Run each permanent regression case independently using its exact `job_key`:

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<topgolf_job_key>"
python -m src.enrichment.production --run --mode backfill --job-key "<toyota_job_key>"
```

Review:

* company and title match
* location match
* canonical employer or ATS URL
* recovered description
* salary and work-model parsing
* match confidence
* score status
* verified score or unresolved reason

Neither role should display a completed low-fit recommendation when evidence remains incomplete.

### Phase 2: Current high-potential queue

Run one bounded backfill:

```powershell
python -m src.enrichment.production --run --mode backfill
```

Review the first 15 high-priority items before increasing any limit.

### Phase 3: Remaining open Gmail roles

Keep the default daily limits until the high-priority queue is accurate. Medium-priority roles should not receive broader processing until the regression and high-priority review is complete.

### Phase 4: Static inventory cleanup

Use the existing source audit and Dashboard source-cleanup queue to close or disable navigation pages, employer information pages, and invalid static records.

## Idempotency expectations

A second identical run should:

* create no duplicate Jobs rows
* create no duplicate deterministic queue rows
* reuse existing search cache evidence
* avoid rewriting unchanged evidence
* avoid changing verified values to weaker values
* refresh Dashboard and Digest without changing job identity

Dashboard writes are expected because the generated timestamp changes. This is presentation refresh, not job-data churn.

## Commands

Preview all eligible production work:

```powershell
python -m src.enrichment.production --dry-run --mode daily
```

Run one job:

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

Override limits for controlled troubleshooting:

```powershell
python -m src.enrichment.production --run --mode backfill --direct-limit 1 --company-limit 1 --external-limit 1 --lifecycle-limit 1
```

Do not increase scheduled limits until the prior phase has been reviewed.
