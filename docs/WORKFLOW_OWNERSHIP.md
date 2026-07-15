# Workflow Ownership Map

## Shared execution rule

All workflows that write the production workbook use:

```yaml
concurrency:
  group: job-tracker-workbook-writes
  queue: max
  cancel-in-progress: false
```

This serializes workbook writes without cancelling an older pending run when a newer run enters the queue.

GitHub Actions cron schedules are defined in UTC. Central-time behavior is controlled by workflow logic where required.

## Pull request checks

### `.github/workflows/pull-request-tests.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Pull Request Tests` |
| Trigger | Pull request to `main`; manual dispatch |
| Inputs | None |
| Outputs | Python compilation result, full pytest result, seven-day `pytest-results` artifact |
| Workbook writes | None |
| Concurrency | Not applicable |
| Expected runtime | Up to 15 minutes |
| Failure implication | The branch has a compilation or test regression and should not merge. |
| Recovery | Inspect the failed test, patch the branch, and let the pull request check rerun. |

### `.github/workflows/regression-readiness.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Regression readiness` |
| Trigger | Pull request; manual dispatch |
| Inputs | None |
| Outputs | Full pytest result and gold-standard regression evaluation |
| Workbook writes | None |
| Concurrency | Not applicable |
| Expected runtime | Full test suite plus regression evaluation; no explicit workflow timeout |
| Failure implication | The branch changed tested behavior or degraded the permanent regression fixture. |
| Recovery | Fix the regression or update the fixture only after reviewed evidence shows the expected result changed intentionally. |

The gold-standard command is:

```powershell
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

## Operational workflows

### `.github/workflows/daily-run.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Daily Run` |
| Trigger | Manual dispatch; two daily UTC schedules for daylight-saving coverage |
| Inputs | Gmail replay mode, selected message IDs, force selected replay, maximum message attempts |
| Outputs | Static ingestion result, Gmail diagnostics, job upsert, generated surfaces, Step Summary, daily attempt and completion records |
| Workbook writes | Schema migration, `Jobs`, source and rejection ledgers, Gmail ledgers, generated surfaces, `Surface_Status`, `Runs` |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 45 minutes |
| Failure implication | The daily completion lock is withheld when required work fails or retryable Gmail failures remain. A daily attempt record is still attempted. |
| Recovery | Correct the failing stage, then manually rerun in normal mode. Use `failed_only` or exact selected replay only for Gmail recovery. |

Scheduled runs check the current Central calendar date. A successful `daily_workflow_completion` record prevents the duplicate schedule from repeating the day. Manual dispatch bypasses the completion lock.

### `.github/workflows/enrichment-run.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Enrichment Run` |
| Trigger | Successful daily workflow on `main`; Sunday schedule; manual dispatch |
| Inputs | `daily`, `weekly`, or `backfill` mode; optional exact `job_key` |
| Outputs | Resolution, enrichment, lifecycle, rescoring, health, generated-surface, and Step Summary metrics |
| Workbook writes | Enrichment queue and evidence, resolution state, canonical `Jobs` updates, lifecycle state, generated surfaces, Dashboard health, `Runs` |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 60 minutes |
| Failure implication | Verification health does not trigger from a failed enrichment workflow. Queue work remains recoverable. |
| Recovery | Rerun the failed mode. Use an exact job key for isolated recovery or backfill mode for bounded backlog work. |

### `.github/workflows/verification-health.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Verification Health` |
| Trigger | Successful enrichment workflow on `main`; manual dispatch |
| Inputs | `run` or `dry-run`; optional deterministic run ID |
| Outputs | Actionable health, portfolio coverage, blockers, aging, classification reasons, Dashboard section, `Runs` history, Step Summary |
| Workbook writes | Dashboard verification section and verification-health `Runs` record in run mode |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 30 minutes, including a 75-second Sheets quota cooldown |
| Failure implication | Operational health is stale or not calculated. Ingestion and enrichment data remain intact. |
| Recovery | Confirm schema validation succeeded, inspect the original traceback, wait for quota recovery when applicable, then rerun in `run` mode. |

### `.github/workflows/weekly-value.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Weekly Value Refresh` |
| Trigger | Monday pre-email schedule; daily refresh schedule; manual dispatch |
| Inputs | Backfill week count, optional data as-of date, optional governance application |
| Outputs | Unified presentation refresh, freshness status, Step Summary |
| Workbook writes | `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, `Digest`, `Surface_Status`, and optional governance surfaces |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 30 minutes |
| Failure implication | One or more generated surfaces may be stale. Canonical `Jobs` data is unchanged. |
| Recovery | Review per-surface status, fix the failing generator, and rerun the workflow. Use governance only when formatting or controls also need repair. |

### `.github/workflows/sheet-governance.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Sheet UX Governance` |
| Trigger | Daily schedule; manual dispatch |
| Inputs | None |
| Outputs | Header colors, dropdowns, filters, freezes, `Sheet_Guide`, Step Summary |
| Workbook writes | Formatting, validation, filters, freezes, and `Sheet_Guide` |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 20 minutes |
| Failure implication | Workbook data remains available, but editability cues or controls may be stale. |
| Recovery | Validate governance definitions and schema, then rerun. Do not manually recreate controls unless the workflow cannot be recovered. |

### `.github/workflows/workbook-capacity.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Workbook Capacity` |
| Trigger | First day of each month; manual dispatch |
| Inputs | Explicit compaction approval and separate blank-formatting trim approval |
| Outputs | Capacity JSON artifact, before-and-after audit, Step Summary |
| Workbook writes | None during scheduled or normal audit. Grid resize requests only during explicitly approved compaction. |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 30 minutes |
| Failure implication | A critical result can indicate workbook writes are at risk. No automatic destructive action occurs. |
| Recovery | Review preservation boundaries, back up the workbook, repair displaced data, and compact only with both approvals when the audit is clean. |

### `.github/workflows/source-quality.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Source Quality` |
| Trigger | Monday schedule; manual dispatch |
| Inputs | Report or reviewed cleanup mode, exact approved company IDs, reporting window, optional skip of live probes |
| Outputs | `Source_Audit`, `Source_Yield`, classifications, recommendations, applied changes, Step Summary |
| Workbook writes | Generated source surfaces, `Runs`, governance; reviewed exact-match `Config_Companies` changes only in approved cleanup mode |
| Concurrency | Shared workbook-write group |
| Expected runtime | Up to 45 minutes |
| Failure implication | Source policy evidence or yield reporting may be stale. Normal source configuration is unchanged unless a reviewed cleanup was already applied. |
| Recovery | Rerun report mode. Cleanup requires live probes, exact company IDs, and exact current source URLs. No-probe mode preserves the last live audit. |

## Workflow chain

```text
Job Tracker Daily Run
        |
        v
Job Tracker Enrichment Run
        |
        v
Job Tracker Verification Health
```

The weekly presentation, governance, source-quality, and capacity workflows are independent scheduled maintenance processes. Shared workbook concurrency prevents simultaneous writes.

## Expected required checks

The expected branch-protection checks are:

1. `Pull Request Tests`
2. `Regression readiness`

The gold-standard regression evaluation is a step inside `Regression readiness`, not a separate check name.

Sprint 52 does not change repository protection settings. Confirm the two exact check names in GitHub branch settings after merge.

## Failure triage order

1. Identify whether the failing workflow writes the workbook.
2. Read the first failing step and original exception, not only the final summary step.
3. Confirm whether canonical `Jobs` writes occurred before retrying.
4. Check `Runs`, `Surface_Status`, `Gmail_Messages`, `Gmail_Failures`, `Enrichment_Queue`, or `Source_Audit` for partial-state evidence.
5. Prefer the smallest safe rerun scope.
6. Never delete audit evidence to make a rerun appear clean.
