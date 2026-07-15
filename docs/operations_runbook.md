# Operations Runbook

This runbook describes the maintenance-mode operating process after Sprint 52.

## Operating principles

1. `Jobs` is the canonical source of truth.
2. Generated surfaces are read-only.
3. Production workbook writes are serialized through the shared GitHub Actions concurrency group.
4. Replays and backfills must use the smallest safe scope.
5. Audit evidence is preserved even when a run fails.
6. Destructive workbook compaction requires explicit approval and a current backup.

## Daily operating cycle

The normal production chain is:

```text
Job Tracker Daily Run
        |
        v
Job Tracker Enrichment Run
        |
        v
Job Tracker Verification Health
```

The daily workflow ingests static and Gmail leads, writes diagnostics and canonical updates, then refreshes generated surfaces. A successful daily completion record is written only when required work completes without retryable Gmail failures.

A successful daily workflow triggers daily enrichment. Successful enrichment triggers verification health.

### Daily review

1. Review failed Action notifications.
2. Confirm the daily, enrichment, and verification-health chain completed.
3. Review `Weekly_Context` for current action items.
4. Review `Review_Queue` and `Follow_Up_Queue` when items are due.
5. Make changes only in green `Jobs` columns or approved configuration sheets.
6. Check `Surface_Status` when any generated surface appears stale.

## Weekly operating cycle

1. Review `Weekly_Value` and `Weekly_Context` after the Monday refresh.
2. Review `Source_Audit` classifications and retry dates.
3. Review `Source_Yield` recommendations and attribution limitations.
4. Resolve manual verification interventions shown in Dashboard or verification health.
5. Review follow-up and application aging.
6. Confirm the Apps Script weekly email was delivered.

No source or search is automatically disabled from one poor week.

## Monthly operating cycle

1. Review the scheduled workbook-capacity audit.
2. Confirm capacity remains below the warning threshold.
3. Review repeated source failures, cooldowns, and manual-review sources.
4. Confirm pull request and regression checks remain healthy.
5. Inspect configuration drift in `Config_Searches`, `Config_Companies`, and `Target_Companies`.
6. Reapply Sheet UX Governance after any approved compaction.

## Quarterly operating cycle

1. Reassess role-level targets, company exclusions, location preferences, and compensation assumptions.
2. Review scoring rules against actual review and application outcomes.
3. Review strategic target-company coverage.
4. Add reviewed examples to the gold-standard regression fixture when needed.
5. Decide whether the next change should be a maintenance patch or a new feature sprint.

A new feature sprint is justified only when the need cannot be handled safely through configuration, documentation, or a focused maintenance patch.

## Manual validation sequence

Run from PowerShell in the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall -q src tests
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.schema --validate
```

Production credential validation:

```powershell
python -m src.workflow_validation
```

`src.workflow_validation` appends a `Runs` record. Do not use it casually against production.

## Manual workflow dispatch sequence

### Daily ingestion recovery

1. Open Actions.
2. Select `Job Tracker Daily Run`.
3. Run on `main`.
4. Use `normal` for an ordinary rerun.
5. Use `failed_only` only when retryable Gmail failures exist.
6. Use `selected` only with exact message IDs.
7. Enable forced selected replay only after confirming the message was already completed and replay is intentional.
8. Review the Step Summary and `Gmail_Failures`.

### Enrichment recovery

1. Select `Job Tracker Enrichment Run`.
2. Use `daily` for normal recovery.
3. Use `weekly` when external-search fallback and broader lifecycle work are required.
4. Use `backfill` for bounded backlog recovery.
5. Supply an exact `job_key` for one-role isolation.

### Generated-surface recovery

1. Select `Job Tracker Weekly Value Refresh`.
2. Leave the as-of date blank for current data.
3. Use the normal backfill window unless historical weekly rows are missing.
4. Enable governance only when formatting, filters, freezes, or dropdowns also need repair.
5. Review `Surface_Status` after completion.

### Verification-health recovery

1. Select `Job Tracker Verification Health`.
2. Use `dry-run` for calculation-only diagnosis.
3. Use `run` to refresh Dashboard health and `Runs` history.
4. Review classification reasons, actionable blockers, and portfolio coverage.

### Source-quality review

1. Select `Job Tracker Source Quality`.
2. Run `report` mode first.
3. Review classifications and exact current URLs.
4. Back up the workbook before cleanup.
5. Use `apply_reviewed_cleanup` only with exact approved company IDs and live probes.
6. Confirm original and final URLs in the Step Summary.

### Workbook-capacity review

1. Select `Job Tracker Workbook Capacity`.
2. Run with both approvals disabled for a read-only audit.
3. Review all preservation boundaries and unknown ranges.
4. Back up the workbook.
5. Enable compaction only after the audit is clean.
6. Enable formatting trim only when formatting-only trailing ranges are intentionally approved.
7. Rerun compaction to confirm idempotency.
8. Run Sheet UX Governance after compaction.

## Change-management process

1. Create one branch and pull request per focused change.
2. Keep future-sprint scope out of the current pull request.
3. Run `Pull Request Tests` and `Regression readiness`.
4. Review the complete diff and unresolved review threads.
5. Squash and merge only after both checks pass.
6. Run the applicable production workflow from `main`.
7. Confirm workbook state, Step Summary, and `Runs` evidence.
8. Document any external limitation or follow-up.

## Maintenance health criteria

The tracker is operationally green when:

1. Daily ingestion completes and writes its completion record.
2. Gmail backlog has no unexplained retryable failures.
3. Production enrichment completes without stuck queue work.
4. Verification health calculates and writes current actionable results.
5. Generated surfaces are current in `Surface_Status`.
6. Workbook capacity is below warning level.
7. Source-quality evidence is current and temporary failures remain recoverable.
8. Pull request tests and gold-standard regression evaluation pass.
9. Schema validation passes.
10. No generated worksheet is being used as a manual data-entry surface.

## Current known limitations

1. Accepted Gmail jobs do not retain durable configured-search IDs, so individual search yield cannot be calculated reliably.
2. Weekly email delivery is owned by Google Apps Script and must be checked separately.
3. Pull request CI cannot perform live workbook writes without production credentials.
4. Branch-protection settings are external repository configuration and require administrative verification.
