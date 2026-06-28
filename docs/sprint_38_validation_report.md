# Sprint 38 Validation Report

Validation date: 2026-06-28

Branch: `codex/sprint-38-lifecycle-readiness`

Pull request: #32

## User-run validation evidence

The local validation run showed:

1. `pytest`: 502 passed.
2. Gold-standard regression evaluation: 19 cases, regression pass rate 1.0, ingestion precision 1.0, ingestion recall 1.0, high-potential recall 1.0, false-closure rate 0.0.
3. Schema migration: success, workbook timezone `America/Chicago`, all expected worksheet headers present.
4. Schema validation: success, workbook timezone `America/Chicago`, all expected worksheet headers present.
5. Workflow validation: success, 17 worksheets validated.
6. Priority lifecycle dry run: 318 jobs read, 50 due jobs shown.
7. Priority lifecycle bounded run: 25 jobs checked, 25 jobs updated, 25 evidence rows written, 0 confirmed closed, 0 expired, 1 likely closed, 1 temporary failure, 0 queue retries, 0 permanent failures.
8. Production readiness before follow-up patch: `not_ready` because 5 high-potential rows were counted as SLA breaches and verification conversion was 0.0.
9. Dashboard refresh: success, 318 jobs read, 317 open jobs, 30 Digest rows, 137 Dashboard rows written, 35 Digest rows written.
10. Final schema validation after Dashboard refresh: success.

## Follow-up patch

The readiness output showed a model problem rather than a code test failure. The readiness evaluator counted high-potential partial rows and enrichment-failure rows as critical SLA breaches even though those rows already have visible blocker states.

Patch applied after validation:

1. `high_priority_sla_breaches` now counts only aged unresolved high-potential jobs that lack a blocker and are not actively in enrichment.
2. Partially verified high-potential rows are treated as blocked rather than hidden.
3. `partial`, `ambiguous`, `not_found`, `retryable_failure`, `permanent_failure`, and `closed` enrichment statuses are treated as visible blockers for the SLA-breach gate.
4. `pending` and `in_progress` are tracked separately as active enrichment, not critical SLA breaches.
5. Warning gates keep warning alert severity even when another critical gate makes the overall readiness classification `not_ready`.
6. Tests were added for blocker handling and alert severity.

## Commands to rerun after follow-up patch

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git fetch origin
git switch codex/sprint-38-lifecycle-readiness
git pull --ff-only origin codex/sprint-38-lifecycle-readiness

.\.venv\Scripts\Activate.ps1
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.production_readiness --evaluate-readiness --dry-run
python -m src.production_readiness --evaluate-readiness --write-run
python -m src.dashboard --no-run-log
python -m src.schema --validate
```

## Expected post-patch outcome

Expected result if the 5 prior high-potential rows are the same 4 partial rows and 1 enrichment-failure row seen in Dashboard:

1. `high_priority_sla_breaches`: 0.
2. `high_priority_unresolved_aged`: 5.
3. `high_priority_blocked`: 5, or 4 blocked and 1 failure depending on row status details.
4. `verification_conversion`: warning may remain because verified high-potential conversion is still 0.0.
5. Readiness classification should become `ready_with_warnings`, not `not_ready`, unless another critical gate fails.

## Remaining program-level risks

1. The workbook still contains low-quality open rows such as talent-community, upload-resume, EEO, and marketing pages. These are not Sprint 38 code blockers, but they reduce tracker signal and should be handled through source cleanup or stricter ingestion gates in a later maintenance sprint.
2. The target-company daily lifecycle queue may spend checks on low-priority target-company rows. This follows the Sprint 38 cadence requirement but may be operationally noisy until low-quality target-company rows are cleaned up.
3. Verification conversion remains low. That is useful as a warning, but it means the tracker is still surfacing high-potential partial evidence more than fully verified reviewable jobs.
4. Several Sheets API quota backoffs occurred. The backoff worked, but quota usage remains a production constraint.
5. The Sprint 38 regression fixture is sanitized and labeled. Real production misses should be added as new cases rather than relying only on the starter dataset.

## Merge recommendation

Do not merge until the post-patch commands above pass locally and the readiness classification is `ready` or `ready_with_warnings` with no failed critical gates.
