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

GitHub Actions cron schedules are defined in UTC. Central-time behavior is listed below for both daylight and standard time. Actual scheduled execution can be delayed by GitHub Actions queueing.

## Pull request checks

### `.github/workflows/pull-request-tests.yml`

| Item | Definition |
| --- | --- |
| Workflow display name | `Pull Request Tests` |
| Check-run job context | `test` |
| Trigger | Pull request to `main`; manual dispatch |
| Schedule | None |
| Inputs | None |
| Outputs | Python compilation result, full pytest result, seven-day `pytest-results` artifact |
| Workbook writes | None |
| Concurrency | Not applicable |
| Configured timeout | 15 minutes |
| Expected runtime | Normally shorter than the timeout; review current Actions history for observed duration |
| Failure implication | The branch has a compilation, test, direct-write contract, or documentation regression and should not merge. |
| Recovery | Inspect the first failing compilation or test step, patch the branch, and let the pull request check rerun. |

### `.github/workflows/regression-readiness.yml`

| Item | Definition |
| --- | --- |
| Workflow display name | `Regression readiness` |
| Check-run job context | `regression-readiness` |
| Trigger | Pull request; manual dispatch |
| Schedule | None |
| Inputs | None |
| Outputs | Full pytest result and gold-standard regression evaluation |
| Workbook writes | None |
| Concurrency | Not applicable |
| Configured timeout | No explicit workflow timeout |
| Expected runtime | Full test suite plus regression evaluation; review current Actions history for observed duration |
| Failure implication | The branch changed tested behavior or degraded the permanent regression fixture. |
| Recovery | Fix the regression or update the fixture only after reviewed evidence shows the expected result changed intentionally. |

The gold-standard command is:

```powershell
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

The direct Sheets write contract is:

```powershell
python -m src.jobs_write_contract --audit --enforce
```

## Operational workflows

### `.github/workflows/daily-run.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Daily Run` |
| Trigger | Manual dispatch; two daily schedules for daylight-saving coverage |
| Schedule | `30 11 * * *` and `30 12 * * *` UTC. These are 06:30 AM and 07:30 AM during Central daylight time, and 05:30 AM and 06:30 AM during Central standard time. The Central-date gate skips a scheduled invocation before 06:30 AM and skips a duplicate after successful completion. |
| Inputs | Gmail replay mode, selected message IDs, force selected replay, maximum message attempts |
| Outputs | Static ingestion result, Gmail diagnostics, job upsert, generated surfaces, Step Summary, daily attempt and completion records |
| Workbook writes | Schema migration, `Jobs`, source and rejection ledgers, Gmail ledgers, generated surfaces, `Surface_Status`, `Runs` |
| Jobs integrity gate | Pre-write enforcement through `src.workflow_validation`; post-write enforcement before `daily_workflow_completion` can be recorded |
| Concurrency | Shared workbook-write group |
| Configured timeout | 45 minutes |
| Expected runtime | Varies with Gmail volume, static sources, tests, and workbook quota; use the timeout as the upper bound |
| Failure implication | The daily completion lock is withheld when required work fails, retryable Gmail failures remain, or Jobs integrity is unsafe. A daily attempt record is still attempted. |
| Recovery | Correct the failing stage, require a healthy Jobs audit, then manually rerun in normal mode. Use `failed_only` or exact selected replay only for Gmail recovery. |

Scheduled runs check the current Central calendar date. A successful `daily_workflow_completion` record prevents the duplicate schedule from repeating the day. Manual dispatch bypasses the completion lock.

### `.github/workflows/enrichment-run.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Enrichment Run` |
| Trigger | Successful daily workflow on `main`; Sunday schedule; manual dispatch |
| Schedule | `0 14 * * 0` UTC, which is 09:00 AM during Central daylight time and 08:00 AM during Central standard time each Sunday |
| Inputs | `daily`, `weekly`, or `backfill` mode; optional exact `job_key` |
| Outputs | Resolution, enrichment, lifecycle, rescoring, health, generated-surface, and Step Summary metrics |
| Workbook writes | Enrichment queue and evidence, resolution state, canonical `Jobs` updates, lifecycle state, generated surfaces, Dashboard health, `Runs` |
| Jobs integrity gate | Explicit pre-write and post-write enforcement steps |
| Concurrency | Shared workbook-write group |
| Configured timeout | 60 minutes |
| Expected runtime | Varies by mode, retry state, and external-source response time; use the timeout as the upper bound |
| Failure implication | Verification health does not trigger from a failed enrichment workflow. Queue work remains recoverable. An integrity failure blocks further writes. |
| Recovery | Preserve integrity diagnostics, require a healthy audit, then rerun the failed mode. Use an exact job key for isolated recovery or backfill mode for bounded backlog work. |

### `.github/workflows/verification-health.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Verification Health` |
| Trigger | Successful enrichment workflow on `main`; manual dispatch |
| Schedule | No independent cron schedule |
| Inputs | `run` or `dry-run`; optional deterministic run ID |
| Outputs | Actionable health, portfolio coverage, blockers, aging, classification reasons, Dashboard section, `Runs` history, Step Summary |
| Workbook writes | Dashboard verification section and verification-health `Runs` record in run mode |
| Jobs integrity gate | Read-only pre-calculation enforcement through `src.workflow_validation`; Verification Health does not mutate `Jobs` |
| Concurrency | Shared workbook-write group |
| Configured timeout | 30 minutes, including a 75-second Sheets quota cooldown |
| Expected runtime | Normally shorter than the timeout unless Sheets quota retries occur |
| Failure implication | Operational health is stale or not calculated. Ingestion and enrichment data remain intact. |
| Recovery | Confirm schema and Jobs integrity validation succeeded, inspect the original traceback, allow quota recovery when applicable, then rerun in `run` mode. |

### `.github/workflows/weekly-value.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Weekly Value Refresh` |
| Trigger | Monday pre-email schedule; daily refresh schedule; manual dispatch |
| Schedule | `0 12 * * 1` UTC on Monday, which is 07:00 AM during Central daylight time and 06:00 AM during Central standard time; `15 14 * * *` UTC daily, which is 09:15 AM during Central daylight time and 08:15 AM during Central standard time |
| Inputs | Backfill week count, optional data as-of date, optional governance application |
| Outputs | Unified presentation refresh, freshness status, Step Summary |
| Workbook writes | `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, `Digest`, `Surface_Status`, and optional governance surfaces |
| Concurrency | Shared workbook-write group |
| Configured timeout | 30 minutes |
| Expected runtime | Varies with workbook size and quota; use the timeout as the upper bound |
| Failure implication | One or more generated surfaces may be stale. Canonical `Jobs` data is unchanged. |
| Recovery | Review per-surface status, fix the failing generator, and rerun the workflow. Use governance only when formatting or controls also need repair. |

### `.github/workflows/sheet-governance.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Sheet UX Governance` |
| Trigger | Daily schedule; manual dispatch |
| Schedule | `0 15 * * *` UTC daily, which is 10:00 AM during Central daylight time and 09:00 AM during Central standard time |
| Inputs | None |
| Outputs | Header colors, dropdowns, filters, freezes, `Sheet_Guide`, Step Summary |
| Workbook writes | Formatting, validation, filters, freezes, and `Sheet_Guide` |
| Jobs integrity gate | Explicit pre-write and post-write enforcement; every Jobs-targeted batch request is boundary validated before submission |
| Concurrency | Shared workbook-write group |
| Configured timeout | 20 minutes |
| Expected runtime | Normally shorter than the timeout; workbook quota can extend execution |
| Failure implication | Workbook data remains available, but editability cues or controls may be stale. Unsafe Jobs width or ranges block governance. |
| Recovery | Require a healthy Jobs audit, validate governance definitions and schema, then rerun. Do not manually recreate controls unless the workflow cannot be recovered. |

### `.github/workflows/workbook-capacity.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Workbook Capacity` |
| Trigger | First day of each month; manual dispatch |
| Schedule | `15 14 1 * *` UTC on the first day of each month, which is 09:15 AM during Central daylight time and 08:15 AM during Central standard time |
| Inputs | Explicit compaction approval and separate blank-formatting trim approval |
| Outputs | Capacity JSON artifact, Jobs integrity JSON, before-and-after audit, Step Summary |
| Workbook writes | None during scheduled or normal audit. Grid resize requests only during explicitly approved compaction. |
| Jobs integrity gate | Read-only pre-run capture and enforced post-run validation. Out-of-bounds data and structural ranges remain preservation boundaries. |
| Concurrency | Shared workbook-write group |
| Configured timeout | 30 minutes |
| Expected runtime | Depends on workbook metadata volume and whether compaction is approved; use the timeout as the upper bound |
| Failure implication | A critical result can indicate workbook writes are at risk. No automatic destructive action occurs. |
| Recovery | Review preservation boundaries, preserve integrity evidence, back up the workbook, repair displaced data, and compact only when the audit is clean. |

### `.github/workflows/source-quality.yml`

| Item | Definition |
| --- | --- |
| Workflow name | `Job Tracker Source Quality` |
| Trigger | Monday schedule; manual dispatch |
| Schedule | `30 13 * * 1` UTC each Monday, which is 08:30 AM during Central daylight time and 07:30 AM during Central standard time |
| Inputs | Report or reviewed cleanup mode, exact approved company IDs, reporting window, optional skip of live probes |
| Outputs | `Source_Audit`, `Source_Yield`, classifications, recommendations, applied changes, Step Summary |
| Workbook writes | Generated source surfaces, `Runs`, governance; reviewed exact-match `Config_Companies` changes only in approved cleanup mode |
| Concurrency | Shared workbook-write group |
| Configured timeout | 45 minutes |
| Expected runtime | Varies with the number and responsiveness of live source probes; use the timeout as the upper bound |
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

## Jobs boundary enforcement ownership

| Control | Owner |
| --- | --- |
| Canonical width and final column | `src.jobs_boundaries` derived from `JOB_FIELDS` |
| Explicit row writes and row placement | `src.sheets.SheetClient` |
| Actual-row upsert cache alignment | `src.job_upsert` |
| Read-only workbook scanner | `src.jobs_integrity` |
| Schema expansion | `src.schema` append-only migration |
| Direct API request guard | `validate_jobs_batch_update_requests` |
| Direct write inventory | `src.jobs_write_contract` and `config/jobs_write_allowlist.yml` |
| Capacity preservation boundary | `src.workbook_capacity_hotfix` |
| Operator response | `docs/JOBS_WRITE_BOUNDARY_INTEGRITY.md` and `docs/TROUBLESHOOTING.md` |

Normal workflows must never expand `Jobs`. The preferred and approved grid width is exactly 135 columns until an intentional append-only `JOB_FIELDS` change is reviewed and migrated.

## Expected required checks

GitHub distinguishes the workflow display name from the job-level check context that branch protection may expose.

| Workflow display name | Current job context |
| --- | --- |
| `Pull Request Tests` | `test` |
| `Regression readiness` | `regression-readiness` |

The gold-standard regression evaluation is a step inside the `regression-readiness` job, not a separate check run.

Sprint 52 does not change repository protection settings. Confirm the required contexts shown in GitHub branch settings against the current pull request checks. Do not assume the workflow display name and required context are identical.

## Workflow YAML validation

`Pull Request Tests` runs the Sprint 52 documentation contract, which parses every current workflow YAML file and verifies the documented workflow inventory, display names, job contexts, and cron schedules. Existing workflow-specific tests continue to validate shell handoffs, concurrency, and workflow behavior.

This repository-level YAML parse catches syntax and inventory drift. GitHub Actions remains the final authority for platform-specific expressions and runtime behavior.

## Failure triage order

1. Identify whether the failing workflow writes the workbook.
2. Read the first failing step and original exception, not only the final summary step.
3. Confirm whether canonical `Jobs` writes occurred before retrying.
4. Run the read-only Jobs integrity audit and preserve any offending coordinates.
5. Check `Runs`, `Surface_Status`, `Gmail_Messages`, `Gmail_Failures`, `Enrichment_Queue`, or `Source_Audit` for partial-state evidence.
6. Prefer the smallest safe rerun scope.
7. Never delete audit evidence or suspicious out-of-bounds cells to make a rerun appear clean.
