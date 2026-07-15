# Sprint 52: Documentation and Maintenance Readiness

## Objective

Sprint 52 consolidates production documentation through Sprint 51, defines workbook and workflow ownership, adds explicit recovery procedures, and establishes the maintenance-mode validation standard.

This sprint does not add a new scoring model, ingestion provider, paid integration, or user-interface redesign.

## Documentation delivered

1. `README.md` now reflects Sprints 1 through 52 and current operating behavior.
2. `docs/WORKBOOK_MAP.md` documents canonical, configurable, audit, system-managed, generated, and user-editable worksheets.
3. `docs/WORKFLOW_OWNERSHIP.md` documents all current GitHub Actions workflows, triggers, schedules, inputs, outputs, writes, concurrency, runtime limits, failure implications, and recovery.
4. `docs/operations_runbook.md` defines daily, weekly, monthly, and quarterly maintenance.
5. `docs/TROUBLESHOOTING.md` provides recovery procedures for the production failure modes identified in the roadmap.
6. Documentation contract tests prevent workflow and worksheet additions from silently becoming undocumented.

## Expected pull request controls

The expected required checks are:

1. `Pull Request Tests`
2. `Regression readiness`

The gold-standard regression evaluation is executed inside `Regression readiness` against:

```text
data/regression/sprint38_gold_standard_jobs.json
```

Sprint 52 does not modify branch-protection settings. Repository administrators must confirm the exact check names in GitHub settings.

## Static readiness assessment

The repository is maintenance-ready when the Sprint 52 branch passes:

```powershell
python -m compileall -q src tests
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

Static readiness covers:

1. Current workflow inventory and documentation.
2. Current worksheet inventory and ownership documentation.
3. Recovery topic coverage.
4. README sprint status and maintenance-mode guidance.
5. Existing unit, workflow-contract, and regression protections.

Static readiness cannot prove live Google Sheets, Gmail, or Apps Script behavior because pull request CI does not receive production credentials.

## Post-merge end-to-end validation

Run these steps from `main` after merge.

### 1. Daily ingestion cycle

1. Manually dispatch `Job Tracker Daily Run` in normal mode.
2. Confirm static ingestion and Gmail ingestion complete.
3. Confirm retryable Gmail failures and backlog are zero or individually explained.
4. Confirm `Gmail_Messages`, `Gmail_Failures`, `Runs`, and the daily completion record are correct.
5. Confirm no duplicate canonical or source rows are created.

### 2. Production enrichment cycle

1. Confirm the successful daily run triggers `Job Tracker Enrichment Run` in daily mode.
2. Confirm stale queue recovery, bounded enrichment, resolution, lifecycle, rescoring, and presentation refresh complete.
3. Confirm no queue item remains stuck without a retry or terminal state.

### 3. Verification-health cycle

1. Confirm successful enrichment triggers `Job Tracker Verification Health`.
2. Confirm actionable health and portfolio coverage are both calculated.
3. Confirm no displayed conversion exceeds 100 percent.
4. Confirm dismissed and terminal roles do not inflate actionable debt.
5. Confirm Dashboard and `Runs` history are updated.

### 4. Unified generated-surface refresh

1. Manually dispatch `Job Tracker Weekly Value Refresh` when an isolated validation is desired.
2. Confirm `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, and `Digest` refresh.
3. Confirm `Surface_Status` records current success, row counts, source run, and data as-of date.
4. Confirm generated surfaces agree with canonical `Jobs` decisions.

### 5. Weekly email contract

1. Confirm `Weekly_Context` contains current action items and weekly metrics.
2. Use the Apps Script test sender.
3. Confirm delivery, formatting, and fallback behavior.

### 6. Governance cycle

1. Manually dispatch `Job Tracker Sheet UX Governance`.
2. Confirm green and gray headers, dropdowns, filters, freezes, and `Sheet_Guide`.
3. Confirm generated surfaces remain read-only.

### 7. Workbook-capacity guard

1. Manually dispatch `Job Tracker Workbook Capacity` with both approvals disabled.
2. Confirm capacity remains below 80 percent.
3. Confirm there are no unexplained preservation boundaries or displaced values.
4. Do not compact unless the audit identifies reclaimable space and a current backup exists.

### 8. Source-quality state

1. Manually dispatch `Job Tracker Source Quality` in report mode with a four-week window and live probes.
2. Confirm `Source_Audit` and `Source_Yield` refresh.
3. Confirm temporary failures remain recoverable.
4. Confirm permanent bad URLs are not retried daily.
5. Confirm configured-search attribution remains labeled unavailable where lineage does not exist.

### 9. Schema and regression validation

1. Confirm both pull request checks passed before merge.
2. Confirm production schema validation succeeds.
3. Confirm Topgolf and Toyota regression cases retain expected behavior.
4. Confirm no stale generated tab remains.

## Final project health report

### Current status

Static repository state is green when the Sprint 52 pull request checks pass. The system has completed the planned maintenance-hardening sequence through workbook capacity, Gmail diagnostics, presentation consistency, actionable health, source quality, and documentation readiness.

### Remaining known limitations

1. Accepted Gmail jobs do not retain durable `Config_Searches.search_id` lineage. Search-level accepted-job yield is therefore not reliable.
2. Weekly email delivery remains a Google Apps Script responsibility outside the GitHub Actions execution chain.
3. Pull request CI cannot validate live workbook writes without production credentials.
4. Branch-protection settings require administrative confirmation.
5. External sources can remain blocked or unavailable despite conservative retry policy.

### Intentionally deferred items

1. Durable configured-search lineage for accepted Gmail jobs.
2. Paid APIs or unrestricted web crawling.
3. Automatic source or search disablement based on short-term yield.
4. Automatic scoring-weight changes from user decisions.
5. A replacement user interface outside Google Sheets.

### Recommended maintenance cadence

1. Daily: ingestion, enrichment, health chain, and current action review.
2. Weekly: Weekly Context, source quality, source yield, and manual interventions.
3. Monthly: capacity, source-health, configuration drift, and regression review.
4. Quarterly: scoring assumptions, exclusions, target companies, search coverage, and regression-fixture review.

### Is another feature sprint justified?

Not by default. After successful post-merge validation, the tracker should move to smaller maintenance patches. A new feature sprint is justified only when a clearly defined capability gap cannot be addressed through configuration, documentation, or a focused patch.

## Acceptance criteria

Sprint 52 is complete when:

1. README and runbooks reflect the production system through Sprint 51 and Sprint 52 documentation work.
2. Every current worksheet and workflow is documented.
3. Recovery procedures cover all roadmap failure modes.
4. Documentation contract tests pass.
5. Pull request tests and regression readiness pass.
6. Post-merge production validation completes or any external limitation is explicitly recorded.
