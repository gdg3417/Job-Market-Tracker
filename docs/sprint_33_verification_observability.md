# Sprint 33: Verification Observability and Health Model

## Purpose

Sprint 33 separates enrichment queue activity from portfolio verification health. An empty queue means no queue item is pending. It does not prove that priority jobs are verified, within service level, lifecycle-current, or ready for a decision.

The implementation reads existing canonical workbook tabs, calculates a reproducible verification snapshot, appends one managed section to `Dashboard`, and upserts one historical row in `Runs`. It does not add a second job-state table or implement later roadmap features.

## Commands

Preview without workbook writes:

```powershell
python -m src.verification_health --dry-run
```

Write the Dashboard section and historical snapshot:

```powershell
python -m src.verification_health --run
```

Optional controls:

```powershell
python -m src.verification_health --dry-run --as-of "2026-06-26T06:00:00-05:00"
python -m src.verification_health --run --run-id "sprint33_controlled_validation"
python -m src.verification_health --run --no-dashboard
python -m src.verification_health --run --no-run-log
```

## Source data and schema

The calculation reads these existing canonical tabs:

* `Jobs`
* `Job_Sources`
* `Enrichment_Queue`
* `Enrichment_Evidence`
* `Runs`
* `Target_Companies`
* `Config_Companies`

No worksheet or canonical header is added. Existing schema migration and validation remain the source of truth. Historical snapshots use structured JSON in the existing `Runs.notes` field.

## Operational run selection

Only these rows can anchor workflow freshness, default snapshot identifiers, or latest-run funnel windows:

* `daily_workflow_completion`, written by the GitHub Actions daily workflow
* `sprint_32_enrichment_*`, or rows whose source is `Production enrichment pipeline`

Schema-validation rows, verification-health rows, and unrelated workflow rows are excluded.

For a daily completion row, the latest-run funnel count covers the full `central_date` in `America/Chicago`. Daily completion rows are written only at the end of the workflow, so their zero-length timestamp pair is not used as the funnel window.

For an enrichment run, the actual start and finish timestamps define the latest-run funnel window.

Source-health attempts and failures always come from the latest enrichment run, even when a newer daily completion row exists.

## Verification funnel

Funnel stages can overlap. The values do not imply a strict one-to-one sequence.

| Stage | Current-count definition | Applicable denominator |
| --- | --- | --- |
| Leads received | Rows in `Job_Sources` | None |
| Jobs normalized | Jobs with job key, company, and title | Leads received |
| Jobs accepted | Normalized jobs that are not excluded | Jobs normalized |
| High-potential jobs identified | Open jobs with `potential_priority=high` | Jobs accepted |
| Enrichment eligible | Open unresolved high-potential or medium-potential high-signal jobs | High-potential jobs identified |
| Enrichment attempted | Jobs with a queue attempt or last-attempt timestamp | Enrichment eligible |
| Authoritative posting found | Jobs with sufficient match confidence and employer, ATS, verified, or employer-like source evidence | Enrichment attempted |
| Evidence accepted | Jobs with accepted enrichment evidence | Authoritative posting found |
| Partially verified | Jobs with `score_status=partially_verified` | Evidence accepted |
| Fully verified | Jobs with `score_status=verified` | Evidence accepted |
| Verified strong fit | Fully verified jobs meeting the configured verified-score threshold | Fully verified |
| Human reviewed | Jobs with a review status or review date | Verified strong fit |
| Applied | Jobs with an application status or application date | Human reviewed |
| Dismissed | Jobs with a dismissal status or reason | Human reviewed |
| Closed | Jobs with a confirmed closed, closed, or expired status | Jobs accepted |

Every stage reports current count, latest operational-run count, latest seven-day count, conversion from the documented denominator, median age, and oldest unresolved age where applicable.

Conversions can exceed 100 percent when stages overlap or use different units. This is exposed rather than hidden.

## Verification aging and service levels

Thresholds are centralized in `config/verification_health.yml`.

| Population | Default service level |
| --- | ---: |
| High-potential unresolved job | 24 hours |
| Target-company unresolved job | 24 hours |
| Medium-potential high-signal job | 72 hours |
| Unresolved enrichment failure | 48 hours |
| Provisional job without a new attempt | 168 hours |

The Dashboard reports current count, median age, oldest age, threshold, and breach count for high-potential provisional jobs, high-potential partially verified jobs, target-company provisional jobs, medium-potential high-signal jobs, enrichment failures, jobs with no authoritative URL, jobs with no successful enrichment attempt, jobs awaiting retry, and manually deferred jobs.

Categories intentionally overlap. The strictest applicable threshold determines the displayed service-level breach for a job.

## Normalized blocker reasons

Every unresolved, nonexcluded job receives one current blocker. Every unresolved high-potential job is retained in the historical blocker map, subject to the configured workbook-safe limit.

Supported values:

* `no_authoritative_url`
* `authoritative_match_below_threshold`
* `source_blocked`
* `source_timeout`
* `source_not_found`
* `parser_failure`
* `missing_description`
* `missing_location`
* `missing_compensation`
* `missing_work_model`
* `retry_scheduled`
* `manual_review_required`
* `no_supported_enrichment_path`
* `enrichment_not_attempted`
* `other`

Detailed queue error text remains in the queue and appears as blocker detail in the Dashboard. Operational causes take precedence over evidence gaps.

## Health components

Sprint 33 retains seven separate component scores. The overall score never replaces supporting metrics.

### Workflow reliability

The score is 100 only when the latest qualifying daily completion or enrichment run succeeded and is within the configured freshness threshold. A missing, failed, or stale qualifying run scores 0 and creates a critical override.

### Ingestion health

When Gmail or daily ingestion values are available:

```text
100 × (1 - (failed rows + Gmail backlog) / max(1, inserted rows + failed rows + Gmail backlog))
```

When no ingestion values are logged, the component receives a neutral score of 50. Unknown ingestion is not treated as healthy.

### Source health

The source failure rate uses attempts and failures from the latest enrichment run for direct-link, company or ATS, and external-search stages. Configured watch and degraded boundaries convert the rate to a 0 to 100 score. No logged enrichment attempts receive a neutral score of 70.

### Verification health

The component combines independently visible service-level and conversion scores:

```text
60% × high-potential service-level score
+ 40% × high-potential fully verified conversion score
```

Queue row count is supporting context only. An empty queue cannot make this component healthy.

### Evidence completeness

The average `evidence_completeness_score` for open high-potential and medium-potential high-signal jobs is mapped through configured boundaries.

### Lifecycle health

Lifecycle eligibility is limited to open jobs that are verified or have an authoritative posting match. Generic LinkedIn, Indeed, or other unverified lead URLs do not make a job lifecycle-eligible.

A qualifying job is stale when its actual lifecycle check timestamp is missing or older than the configured threshold. General job `updated_at` timestamps are not treated as lifecycle checks.

One timeout, blocked response, parser failure, rate limit, or nonauthoritative miss does not close a job.

### Decision-readiness health

The decision-ready rate is verified strong-fit priority jobs divided by open high-potential and medium-potential high-signal jobs. Missing evidence remains an unresolved blocker rather than becoming a negative fit decision.

### Overall score and classification

The numerical score is the lower of the arithmetic average of the seven component scores and the lowest component score plus 20 points.

| Score | Classification |
| ---: | --- |
| 85 to 100 | Healthy |
| 60 to 84 | Watch |
| 40 to 59 | Degraded |
| 0 to 39 | Blocked |

The worst component classification limits the overall classification. A critical workflow override forces `Blocked` and caps the overall score at 20.

## Dashboard behavior

The command preserves the existing action-oriented Dashboard. It manages only rows between these markers:

* `Verification observability`
* `End verification observability`

The managed section contains overall health, critical overrides, component scores, the verification funnel, aging, service-level breaches, blocker counts, oldest unresolved high-potential jobs, oldest unresolved target-company jobs, and jobs requiring manual intervention.

The prior managed block is removed before replacement. Repeated runs do not duplicate the Dashboard section.

## Historical `Runs` behavior

The default run identifier is based on the latest qualifying daily completion or enrichment run:

```text
sprint33_verification_health_<latest operational run id>
```

If no qualifying operational run exists, the UTC date is used. An explicit `--run-id` overrides the default.

A repeated calculation for the same run identifier updates the existing `Runs` row rather than appending another row. The compact JSON in `notes` includes funnel metrics, aging metrics, component scores, blocker counts, the bounded high-potential blocker map, service-level breach count, thresholds, critical overrides, and workbook row counts.

## Diagnosis guide

### Workflow reliability is blocked

Review the latest daily completion and enrichment runs. Schema-validation success does not satisfy this component.

### Ingestion health is degraded

Review Gmail backlog, failed messages, and ingestion run notes. Do not weaken quality gates solely to reduce rejected rows.

### Source health is degraded

Review the latest enrichment run and distinguish blocked, timeout, parser, not-found, and unsupported-path failures.

### Verification health is degraded

Start with the oldest high-potential service-level breaches. Resolve `enrichment_not_attempted`, `no_authoritative_url`, and ambiguous-match blockers before lower-priority evidence gaps.

### Evidence completeness is degraded

Use blocker counts to identify missing description, location, compensation, or work-model evidence. Estimated evidence must not be treated as authoritative.

### Lifecycle health is degraded

Review actual lifecycle timestamps and workflow execution. Preserve conservative closure safeguards.

### Decision-readiness health is degraded

Compare open priority jobs with fully verified strong fits. An empty enrichment queue does not resolve this condition.

## Queue backlog versus verification health

Queue backlog answers how many deterministic enrichment work items are pending.

Verification health answers how many priority jobs remain unresolved, how old they are, why they are unresolved, and how many are decision-ready.

A queue can be empty because jobs were never queued, retries were exhausted, a path is unsupported, evidence remains incomplete, or a match remains ambiguous.

## Production validation

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"

git switch codex/sprint-33-verification-observability
git pull --ff-only origin codex/sprint-33-verification-observability

.\.venv\Scripts\Activate.ps1
pytest
python -m src.schema --validate
python -m src.verification_health --dry-run
python -m src.verification_health --run --run-id "sprint33_controlled_production_validation"
```

Review the output and workbook:

1. Confirm workflow reliability references a daily completion or enrichment run, not a Sprint 16 validation row.
2. Confirm source health references the latest enrichment run and reports its attempt count.
3. Confirm lifecycle eligibility excludes generic unverified job-board URLs.
4. Confirm the existing Dashboard action queue, Tracker health, Top roles, and Source cleanup sections remain.
5. Confirm the new funnel, aging, service-level, blocker, and oldest-job sections appear once.
6. Confirm the `Runs` row uses `sprint33_controlled_production_validation`.
7. Rerun the same command and confirm the existing row is updated rather than duplicated.
8. Confirm no `Jobs`, `Dashboard`, `Digest`, or `Runs` rows are duplicated.

Run one bounded production refresh:

```powershell
python -m src.enrichment.production --run --mode daily --direct-limit 3 --company-limit 3 --external-limit 0 --lifecycle-limit 0
python -m src.verification_health --run
```

Confirm the new health snapshot references the latest enrichment run and existing `Digest` content remains functional.

## Rollback

Sprint 33 adds no workbook columns or worksheets.

1. Disable `Job Tracker Verification Health`.
2. Revert the Sprint 33 pull request.
3. Refresh the existing Dashboard through the normal production enrichment cycle.
4. Retain historical Sprint 33 `Runs` rows for auditability unless removal is explicitly approved.

## Intentionally deferred

* Sprint 34 authoritative posting resolution and manual URL overrides
* Sprint 35 structured ATS connectors and source-state controls
* Sprint 36 durable human review and application fields
* Sprint 37 compensation, benefits, work-model, and commute evidence
* Sprint 38 lifecycle evaluation, regression precision and recall, readiness gates, and alerting
