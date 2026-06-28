# Production Readiness Runbook

This guide covers daily, weekly, and exception handling for the Job Market Tracker after Sprints 33 through 38.

## Daily monitoring

Run or inspect the scheduled daily workflow.

Minimum checks:

1. Daily workflow completed within the freshness threshold.
2. Schema validation passed.
3. Gmail backlog is not above threshold.
4. High-potential jobs do not breach service level.
5. Dashboard and Digest refreshed.
6. Alerts from production readiness are actionable and not duplicated.

Command:

```powershell
python -m src.production_readiness --evaluate-readiness --dry-run
```

If the output is `not_ready`, inspect failed critical gates before acting on new job recommendations.

## Weekly monitoring

Run the weekly enrichment and lifecycle pass, then evaluate readiness.

```powershell
python -m src.enrichment.production --run --mode weekly
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.production_readiness --evaluate-readiness --write-run
python -m src.dashboard --no-run-log
```

Review:

1. Source failures by platform.
2. Resolution success rate.
3. Verification conversion rate.
4. Lifecycle closure and reopen events.
5. Regression cases that failed or became stale.

## Health interpretation

Use component metrics rather than one aggregate score.

Critical gates require immediate attention:

1. Daily workflow stale.
2. Schema invalid.
3. High-priority service-level breaches.
4. False closure count above zero.
5. Regression pass rate below 100 percent.
6. Dashboard refresh failure.
7. Digest refresh failure.

Warning gates should be reviewed but do not automatically block use of the tracker:

1. Resolution success below threshold.
2. Verification conversion below threshold.
3. Source failure rate above threshold.
4. Enrichment backlog above threshold.
5. Gmail backlog above threshold.

## Manual review workflow

For each role in the review queue:

1. Confirm the role is still open through an authoritative URL when possible.
2. Review compensation, work model, commute, and benefits evidence.
3. Set `review_status` and `interest_decision`.
4. Use `manual_priority` only when the automated priority should not control queue ordering.
5. Add `dismissal_reason` when dismissing.
6. Add application fields when applying.
7. Rerun Dashboard and production readiness.

Do not erase automated evidence when entering manual evidence.

## Source recovery

When a platform or source fails:

1. Check whether failures are widespread or isolated.
2. Do not disable a source from one timeout, one 429, one 5xx response, or one parser failure.
3. Inspect `Source_Health` and recent `Runs` notes.
4. Correct invalid configuration first.
5. Temporarily pause chronic failures when thresholds are breached.
6. Re-enable after a successful smoke test.

## Resolver troubleshooting

If a high-potential role has no authoritative URL:

1. Confirm company aliases and parent company are configured.
2. Confirm ATS platform and board token where applicable.
3. Review `Posting_Resolution` state.
4. Review `Resolution_Candidates` score components.
5. Use manual authoritative URL only when the match is visible and auditable.
6. Rerun bounded resolver or enrichment.

Low-confidence candidates must remain reviewable and should not merge evidence.

## ATS connector troubleshooting

For structured connectors:

1. Confirm platform, company ID, and board token.
2. Run the inventory command.
3. Run connector smoke tests.
4. Check pagination and rate-limit settings.
5. Confirm normalized errors are recorded.
6. Confirm a temporary failure did not close any job.

## Compensation evidence handling

Confirmed compensation may come only from:

1. Employer-posted evidence.
2. Recruiter-provided evidence.
3. Application-form evidence.
4. Government disclosure.
5. Explicit user-entered evidence.

Estimated compensation must remain labeled as estimated. Missing compensation should create a follow-up need, not a low-quality decision by itself.

## Lifecycle review

A role can close only from:

1. Explicit authoritative closed status.
2. Expired authoritative `validThrough`.
3. Repeated authoritative absence on later checks.
4. Manual closure.

Never close from one timeout, one blocked source, one parser failure, one rate-limit response, one non-authoritative 404, one empty search result, or one external-search miss.

When a role reopens, preserve prior closure history and reset review only if the user explicitly decides to do so.

## Regression evaluation

Run:

```powershell
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

A failing regression should produce one of these actions:

1. Fix ingestion, resolution, scoring, evidence, or lifecycle logic.
2. Update the labeled fixture only when the expected answer has changed.
3. Add a new fixture case when a new failure mode is discovered.

Do not remove Topgolf or Toyota from the dataset.

## Production readiness gates

Run:

```powershell
python -m src.production_readiness --evaluate-readiness --write-run
```

Expected classifications:

1. `ready`: production use is acceptable.
2. `ready_with_warnings`: production use is acceptable, but warning gates should be reviewed.
3. `not_ready`: do not rely on new recommendations until failed critical gates are resolved.

## Rollback procedures

Code rollback:

```powershell
git switch main
git pull --ff-only origin main
git revert <merge_commit_sha>
pytest
python -m src.schema --validate
```

Workbook rollback:

1. Do not delete historical `Runs` rows unless explicitly required.
2. Do not delete user-entered review, application, or compensation evidence.
3. Leave appended schema fields in place unless a separate cleanup is approved.
4. If Dashboard or Digest output is wrong, rerun the prior known-good version from `main` after validation.

## Production validation report template

Use this format after Sprint 38 validation:

```text
Date:
Branch:
Commit:
pytest:
Regression evaluation:
Schema migrate:
Schema validate:
Workflow validation:
Lifecycle dry run:
Bounded lifecycle run:
Missed-role audit:
Production readiness classification:
Alerts reviewed:
Dashboard refresh:
Digest refresh:
Topgolf regression:
Toyota regression:
False closures found:
Duplicate rows found:
Remaining warnings:
Merge recommendation:
```
