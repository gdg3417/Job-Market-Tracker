# Job Market Tracker

A zero added cost Python, GitHub Actions, Google Sheets, Gmail, and Google Apps Script tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It prioritizes roles that can improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term progression toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 52 and the associated production hotfixes are implemented on the current branch.

The tracker is in maintenance mode. The core operating system now includes:

1. Static company, public ATS, Gmail, LinkedIn, and job-alert ingestion.
2. Normalization, deduplication, rejected-lead handling, and source provenance.
3. Potential priority separated from verified fit.
4. Authoritative posting resolution and structured ATS connectors.
5. Evidence completeness, verified scoring, and conservative lifecycle monitoring.
6. Human review, application tracking, compensation, work-model, benefits, commute, and next-action fields.
7. Generated Review Queue, Follow-Up Queue, Weekly Value, Weekly Context, Dashboard, and Digest surfaces.
8. Shared Google Sheets date normalization and one deterministic presentation refresh.
9. Actionable verification health separated from historical portfolio coverage.
10. Gmail message-level failure diagnostics, bounded replay, and quarantine controls.
11. Workbook-capacity auditing, explicit compaction, and capacity thresholds.
12. Source-quality cooldowns, live source audit, and four-week source-yield reporting.
13. Sheet governance with green editable headers, gray system headers, filters, freezes, and dropdowns.
14. Pull request tests, regression readiness, workflow YAML contracts, and permanent Topgolf and Toyota regression cases.
15. Fail-closed `Jobs` write boundaries, explicit `A:EE` row writes, and read-only out-of-bounds integrity auditing.

`Jobs` remains the canonical source of truth. Generated worksheets are read-only and are rebuilt from canonical data.

## Canonical Jobs write boundary

`Jobs` contains exactly the fields in `src.models.JOB_FIELDS`.

Current production contract:

* 135 canonical fields
* Canonical range `A:EE`
* Final field `decision_evidence_conflict_notes`
* Approved grid width 135 columns
* No populated value, formula, note, validation, hyperlink, smart chip, rich text, chart, slicer, filter, protected range, named range, or other hard metadata after `EE`

Normal job creation and updates write explicit ranges such as `Jobs!A2:EE2`. New row placement is calculated from canonical identity fields inside `A:EE`, not the Google Sheets used range. Normal workflows cannot expand the Jobs grid.

Run the read-only scanner:

```powershell
python -m src.jobs_integrity --audit
python -m src.jobs_integrity --audit --enforce
```

Audit reviewed direct Sheets writes:

```powershell
python -m src.jobs_write_contract --audit --enforce
```

The integrity scanner does not repair, delete, compact, or widen the workbook. An unsafe result blocks writes and preserves evidence for investigation. See `docs/JOBS_WRITE_BOUNDARY_INTEGRITY.md`.

## Operating model

```text
Email, ATS, or company-site lead
        |
Normalize, deduplicate, and record provenance
        |
Apply exclusions and assign potential priority
        |
Resolve an authoritative employer or ATS posting
        |
Run bounded enrichment and lifecycle checks
        |
Merge accepted evidence and calculate verified fit
        |
Write canonical Jobs state
        |
Refresh Review Queue, Follow-Up Queue, Weekly Value,
Weekly Context, Dashboard, Digest, and Surface Status
        |
Calculate actionable verification health
        |
Audit source quality, yield, workbook capacity, and Jobs integrity
```

Potential priority is not a final score. Missing evidence reduces completeness and confidence, not role quality.

## Manual review workflow

1. Use `Review_Queue`, `Follow_Up_Queue`, `Weekly_Context`, and `Dashboard` to identify work.
2. Make review, interest, application, follow-up, compensation, work-model, and notes changes only in green columns on `Jobs`.
3. Use `Config_Searches`, `Config_Companies`, `Scoring_Rules`, and `Target_Companies` for approved configuration changes.
4. Do not edit generated surfaces. Their contents will be overwritten on refresh.
5. Run the unified presentation refresh after material manual edits when an immediate update is needed.
6. Use the documented `Posting_Resolution` manual fields only for a validated authoritative-posting override.
7. Do not add, delete, reorder, or widen `Jobs` columns outside an approved append-only schema migration.

The complete worksheet ownership, edit map, and manual authoritative URL distinction are in `docs/WORKBOOK_MAP.md`.

## Local setup

Run from PowerShell:

```powershell
cd $env:USERPROFILE\Desktop
git clone https://github.com/gdg3417/Job-Market-Tracker.git
cd Job-Market-Tracker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
pytest
```

## Core validation

```powershell
python -m compileall -q src tests
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.jobs_write_contract --audit --enforce
python -m src.schema --validate
python -m src.jobs_integrity --audit --enforce
python -m src.workflow_validation
```

`src.workflow_validation` requires production Google Sheets credentials, enforces Jobs integrity, and appends a `Runs` row. Use it through a production workflow when local credentials are unavailable.

Use header repair only when workbook structure is incorrect:

```powershell
python -m src.schema --repair-headers
python -m src.schema --validate
```

Schema migration and repair fail closed on an oversized Jobs grid. They do not compact or delete suspicious trailing cells.

## Primary operating commands

### Gmail ingestion

```powershell
python -m src.gmail_ingestion --run
python -m src.gmail_ingestion --run --retry-failed-only
python -m src.gmail_ingestion --run --message-id "<exact_message_id>"
```

Force replay is restricted to explicitly selected message IDs. Do not broadly replay completed Gmail messages.

### Static ingestion and job upsert

```powershell
python -m src.main --static-pages-smoke-test
python -m src.main --job-upsert-smoke-test
```

### Production enrichment

```powershell
python -m src.enrichment.production --dry-run --mode daily
python -m src.enrichment.production --run --mode daily
python -m src.enrichment.production --run --mode weekly
python -m src.enrichment.production --run --mode backfill
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

### Unified generated-surface refresh

```powershell
python -m src.presentation_refresh --refresh --source-run "manual-maintenance"
python -m src.presentation_refresh --refresh --source-run "manual-maintenance" --governance
```

The authoritative refresh order is:

1. `Review_Queue`
2. `Follow_Up_Queue`
3. `Weekly_Value`
4. `Weekly_Context`
5. `Dashboard`
6. `Digest`
7. Optional governance
8. `Surface_Status`

### Verification health

```powershell
python -m src.verification_health --dry-run
python -m src.verification_health --run
```

Verification health prioritizes currently actionable roles. Historical portfolio evidence coverage is reported separately.

### Source quality and yield

```powershell
python -m src.source_quality_report --dry-run --weeks 4
python -m src.source_quality_report --write-report --weeks 4
python -m src.source_quality_report --write-report --weeks 4 --skip-live-probes
```

Reviewed source cleanup requires live probes and exact approved `Config_Companies.company_id` values.

### Workbook capacity

```powershell
python -m src.workbook_capacity_hotfix --audit --enforce-critical
python -m src.workbook_capacity_hotfix --compact --apply --enforce-critical --allow-trim-blank-formatting
```

Compaction is never part of normal daily execution. It requires explicit apply approval. Formatting-only removal requires separate approval. Real out-of-bounds Jobs data and structural metadata remain preservation boundaries.

## GitHub Actions

Operational workflow ownership, exact cron schedules, Central-time behavior, inputs, workbook writes, failure implications, Jobs integrity gates, and recovery procedures are documented in `docs/WORKFLOW_OWNERSHIP.md`.

All production workbook writers use the shared `job-tracker-workbook-writes` concurrency group with queued, non-cancelling execution.

Pull request validation uses these workflows and job-level contexts:

| Workflow display name | Job-level check context |
| --- | --- |
| `Pull Request Tests` | `test` |
| `Regression readiness` | `regression-readiness` |

`Regression readiness` includes the gold-standard regression evaluation against `data/regression/sprint38_gold_standard_jobs.json`.

The Sprint 52 documentation contract parses every current workflow YAML file and verifies workflow inventory, display names, job contexts, and cron schedules. Existing workflow-specific tests validate shell handoffs, concurrency, and behavior.

Repository branch-protection settings are administrative configuration. Confirm the required contexts shown in GitHub settings against the current pull request checks. Do not assume the workflow display name and branch-protection context are identical.

## Maintenance cadence

### Daily

1. Review failed GitHub Actions notifications.
2. Review `Weekly_Context`, `Review_Queue`, and `Follow_Up_Queue` when action is due.
3. Confirm the daily run and its triggered enrichment and verification-health chain completed.
4. Treat any Jobs integrity gate failure as a production incident.

### Weekly

1. Review Weekly Context and Weekly Value.
2. Review `Source_Audit` and `Source_Yield` recommendations.
3. Resolve current manual verification interventions.
4. Check `Surface_Status` for stale or failed generated surfaces.

### Monthly

1. Review the workbook-capacity audit.
2. Inspect repeated source failures and cooldowns.
3. Confirm regression checks continue to pass on merged changes.
4. Review configuration drift and stale manual follow-up dates.
5. Confirm Jobs remains exactly 135 columns with zero out-of-bounds evidence.

### Quarterly

1. Reassess scoring assumptions and role-level preferences.
2. Review blocked companies, target companies, search coverage, and source strategy.
3. Expand the gold-standard regression fixture only with reviewed examples.
4. Decide whether a new feature sprint is justified or a smaller maintenance patch is sufficient.

The detailed maintenance runbook is in `docs/operations_runbook.md`.

## Recovery and troubleshooting

Use `docs/TROUBLESHOOTING.md` for explicit procedures covering:

1. Gmail backlog, credentials, replay, and duplicate concerns.
2. Google Sheets quota exhaustion and workbook-capacity warnings.
3. Jobs out-of-bounds integrity failures.
4. Verification-health failures.
5. Stale or partially refreshed generated surfaces.
6. Static source failures and source-quality cooldowns.
7. Schema mismatch and header repair.
8. Failed enrichment and lifecycle work.
9. Authoritative posting resolution and manual override handling.
10. Failed Weekly Context email delivery.

## Documentation

* `docs/JOBS_WRITE_BOUNDARY_INTEGRITY.md`
* `docs/WORKBOOK_MAP.md`
* `docs/WORKFLOW_OWNERSHIP.md`
* `docs/operations_runbook.md`
* `docs/TROUBLESHOOTING.md`
* `docs/production_readiness_runbook.md`
* `docs/sprint_47_workbook_capacity.md`
* `docs/sprint_48_gmail_ingestion_recovery.md`
* `docs/sprint_49_generated_surface_consistency.md`
* `docs/sprint_50_actionable_verification_health.md`
* `docs/sprint_51_source_quality_yield.md`
* `docs/sprint_52_documentation_readiness.md`

## Sprint implementation status

| Sprint | Status | Main addition |
| --- | --- | --- |
| 1 to 12 | Complete | Core ingestion, Sheets, scoring, deduplication, Dashboard, and workflow foundation |
| 13 to 25 | Complete | Schema safety, quality gates, source audit, Gmail ledger, weekly email, and parser recovery |
| 26 to 35 | Complete | Potential priority, enrichment, verified scoring, lifecycle, authoritative resolution, and ATS reliability |
| 36 to 40 | Complete | Human review, decision evidence, production readiness, and verification-health hotfixes |
| 41 to 46 | Complete | Review Queue, follow-up aging, Weekly Value, Weekly Context, and sheet governance |
| 47 | Complete | Workbook capacity and grid safety |
| 48 | Complete | Gmail recovery and message-level diagnostics |
| 49 | Complete | Unified generated-surface refresh and shared date handling |
| 50 | Complete | Actionable verification health and corrected funnel semantics |
| 51 | Complete | Source quality, cooldowns, and four-week yield reporting |
| 52 | Complete | Documentation consolidation and maintenance readiness |
| Post-52 hotfix | Implemented on branch | Canonical Jobs write boundary, integrity scanner, workflow gates, and direct-write contract |

## Current known limitations

1. Accepted Gmail jobs do not yet retain durable `Config_Searches.search_id` lineage, so individual configured-search yield remains unavailable.
2. Weekly email delivery is handled by Google Apps Script and is operationally separate from GitHub Actions.
3. Live workbook readiness must be validated after merge because production credentials are not available to pull request CI.
4. Branch-protection configuration must be verified in GitHub repository settings by an administrator.
5. `Posting_Resolution` manual override fields are a narrow legacy exception to the green-header convention. Use only the fields documented in `docs/WORKBOOK_MAP.md`.
