# Sprint 38 Lifecycle Maturity, Regression Evaluation, and Production Readiness

Sprint 38 adds an explicit production-readiness layer for the completed health-improvement roadmap. It does not loosen existing enrichment, evidence, resolver, review, compensation, or lifecycle safeguards.

## Scope

Implemented capabilities:

1. Priority-based lifecycle cadence policy.
2. Conservative closure decision helper.
3. Reopened detection helper and lifecycle history record builder.
4. Version-controlled gold-standard regression dataset.
5. Precision, recall, conversion, and false-closure metric calculation.
6. Missed-role audit helper for source alerts and accepted jobs.
7. Production-readiness gates with critical override behavior.
8. Low-noise alert generation with deduplication.
9. Production readiness run record generation for the existing `Runs` tab.
10. CLI entry point at `python -m src.production_readiness`.

The existing `src.enrichment.lifecycle` runner remains the authoritative lifecycle execution path. Sprint 38 adds a readiness and evaluation layer around it so future production runs can be judged against explicit gates.

## Priority-based lifecycle cadence

Default cadence policy:

| Job class | Frequency |
| --- | ---: |
| High-potential jobs | Daily |
| Target-company jobs | Daily |
| Interested or applied jobs | Daily |
| Other reviewed jobs | Weekly |
| Low-priority provisional jobs | Every 14 days |
| Closed jobs | Every 30 days |
| Other jobs | Weekly |

The policy is represented by `LifecycleCadencePolicy` and can be overridden by passing a dictionary or object to production-readiness helpers. The selection helper is deterministic and sorts due jobs by lifecycle importance, due date, company, and title.

## Closure safety rules

A job may be treated as closed only when one of these conditions is present:

1. Manual closure decision.
2. Explicit authoritative closed or inactive status.
3. Authoritative `validThrough` date has passed.
4. Repeated authoritative absence reaches the configured threshold.

A job cannot be closed from one timeout, one blocked source, one parser failure, one rate-limit response, one temporary server failure, one external-search miss, or one empty search result.

When a closure protection blocks a risky update, `ClosureDecision.safeguard_triggered` is set to `True` so the alerting layer can surface the event without changing the job state.

## Reopened detection

`detect_reopened` returns true only when a previously terminal or likely closed job receives an authoritative listed observation that is not explicitly closed.

The lifecycle history record builder preserves:

1. Previous status.
2. Next status.
3. First observed date.
4. Last authoritative observation.
5. Retrieval success or failure timestamps.
6. Consecutive authoritative absence count.
7. Closure reason and confidence.
8. Reopened date.
9. Closure evidence source.

This preserves closure history rather than erasing it when a requisition reopens.

## Gold-standard regression dataset

The Sprint 38 dataset lives at:

```text
data/regression/sprint38_gold_standard_jobs.json
```

It includes sanitized cases for:

1. Topgolf.
2. Toyota.
3. Strong strategy roles.
4. Strong business operations roles.
5. Product and category roles with P&L paths.
6. Pure FP&A roles that should rank lower.
7. Duplicate jobs from multiple sources.
8. Recruiting intermediary posts.
9. Generic job-alert metadata.
10. Sparse alerts requiring enrichment.
11. Closed postings.
12. Reopened postings.
13. Remote roles.
14. Hybrid or commute-sensitive roles.
15. Five-day on-site roles.
16. Confirmed compensation.
17. Estimated compensation.
18. Missing compensation.
19. Ambiguous authoritative matches.
20. Blocked sources.
21. Temporary source failures.

Run:

```powershell
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

## Evaluation metrics

Automatically generated metrics include:

1. Ingestion precision.
2. Ingestion recall.
3. Duplicate precision.
4. Resolution success rate.
5. Authoritative-match precision.
6. Evidence-acceptance precision.
7. High-potential recall.
8. Verified-strong-fit precision.
9. Closure precision.
10. Closure recall.
11. False-closure rate.
12. Review conversion.
13. Application conversion.
14. Regression pass rate.

Metrics are calculated from labeled cases. Production precision and recall are only as reliable as the labels in the fixture. Expand the dataset when a real production failure is found.

## Missed-role audit

The missed-role audit compares selected source alert rows with accepted job rows. It flags:

1. Missed jobs.
2. Incorrectly rejected jobs.
3. Duplicate collapse problems.
4. Incorrect company normalization.
5. Incorrect title normalization.
6. Incorrect priority classification.

The audit is controlled and fixture-backed. It does not require unrestricted scraping.

## Production-readiness gates

Readiness gates:

| Gate | Default threshold | Critical |
| --- | --- | --- |
| Daily workflow freshness | 30 hours or less | Yes |
| Schema validity | Must be true | Yes |
| Gmail backlog | 0 | No |
| Enrichment backlog | 25 or less | No |
| High-priority service-level breaches | 0 | Yes |
| Resolution success rate | At least 50 percent | Warning |
| Verification conversion rate | At least 25 percent | Warning |
| Source failure rate | 25 percent or less | Warning |
| Lifecycle false-closure count | 0 | Yes |
| Regression pass rate | 100 percent | Yes |
| Dashboard refresh success | Must be true | Yes |
| Digest refresh success | Must be true | Yes |

Readiness classifications:

1. `ready`: every gate passes.
2. `ready_with_warnings`: warning-level gates need attention, but critical gates pass.
3. `not_ready`: any failure gate exists, or any critical gate fails.

Critical failures override aggregate metrics.

## Alerts

The alerting layer emits alerts only for failed gates and selected warning gates. It deduplicates by alert ID so a persistent condition does not create duplicate alerts every time the command runs.

Alert categories include:

1. Daily workflow not completed recently.
2. Gmail backlog above threshold.
3. High-potential job unresolved beyond service level.
4. New enrichment or lifecycle failure requiring manual review.
5. Source platform broadly failing.
6. Lifecycle false-closure safeguard triggered.
7. Production readiness becoming not ready.

The implementation intentionally avoids alerting for every minor source failure.

## Commands

Run regression evaluation:

```powershell
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

Run workbook-backed production readiness without writing a `Runs` row:

```powershell
python -m src.production_readiness --evaluate-readiness --dry-run
```

Run workbook-backed production readiness and append one `Runs` row:

```powershell
python -m src.production_readiness --evaluate-readiness --write-run
```

## Production validation sequence

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
git fetch origin
git switch codex/sprint-38-lifecycle-readiness
git pull --ff-only origin codex/sprint-38-lifecycle-readiness

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation
python -m src.enrichment.lifecycle --dry-run
python -m src.enrichment.lifecycle --run --limit 25
python -m src.production_readiness --evaluate-readiness --dry-run
python -m src.production_readiness --evaluate-readiness --write-run
python -m src.dashboard --no-run-log
python -m src.schema --validate
```

After the commands complete, verify:

1. No Topgolf or Toyota regression case failed.
2. No job was closed from one timeout, one blocked response, one parser failure, one rate-limit response, one non-authoritative 404, or one external-search miss.
3. Reopened roles preserve prior closure history in evidence and notes.
4. The readiness classification is `ready` or `ready_with_warnings`.
5. Any alerts are low-noise and tied to readiness gates.
6. `Runs` contains only one row for a given production-readiness timestamp.
7. Dashboard and Digest refresh successfully.

## Rollback

Revert the Sprint 38 pull request. If a production-readiness row has already been appended to `Runs`, leaving it in place is low risk because it is historical metadata. Do not delete lifecycle, enrichment, resolver, review, compensation, or application evidence.

## Deferred

The following items were considered and intentionally deferred:

1. Paid live routing APIs.
2. Automatic scoring-weight changes based on regression results.
3. Unrestricted crawling for recall audits.
4. Disabling sources automatically from one production-readiness failure.
5. Sending external notifications outside the existing workbook and CLI workflow.
