# Job Market Tracker

A zero added cost Python, GitHub Actions, Google Sheets, Gmail, and Google Apps Script tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It prioritizes roles that can improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term progression toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 32 are implemented in code.

The system now supports:

1. Static company and public ATS ingestion
2. Gmail and LinkedIn digest ingestion
3. Normalization, deduplication, and source provenance
4. Potential priority separated from verified fit
5. Evidence completeness and score-state tracking
6. Direct-link, company, ATS, and controlled external-search enrichment
7. Authoritative match validation and safe evidence merging
8. Verified scoring with company context
9. Enrichment retries and posting lifecycle monitoring
10. Production daily and weekly enrichment workflows
11. Stale `in_progress` recovery after interrupted workflows
12. Dashboard, Digest, and weekly email presentation
13. Workbook schema migration and validation
14. Gmail message and rejected-job ledgers
15. Source quality auditing and static inventory cleanup

Topgolf `Sr Manager, Strategic Planning` and Toyota North America `National Manager, Product` are permanent regression cases.

## Operating model

```text
Email or website lead
        |
Normalize and deduplicate
        |
Assign potential priority
        |
Queue high-potential sparse jobs
        |
Direct URL enrichment
        |
Company career page or ATS enrichment
        |
External search fallback
        |
Match confidence validation
        |
Merge verified evidence
        |
Complete scoring
        |
Dashboard, Digest, and lifecycle monitoring
```

Potential priority is not a final score. Missing evidence reduces completeness and confidence, not role quality.

## Workbook tabs

The canonical schema is managed in `src/schema.py`.

```text
Config_Searches
Config_Companies
Scoring_Rules
Target_Companies
Jobs
Job_Sources
Rejected_Jobs
Gmail_Messages
Enrichment_Queue
Enrichment_Evidence
Snapshots
Runs
Digest
Dashboard
```

`Enrichment_Queue` records deterministic work items. `Enrichment_Evidence` stores extracted evidence, match confidence, acceptance status, source URL, retrieval time, and content hash without storing full raw HTML.

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
pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation
python -m src.source_audit
```

Use header repair only when the workbook structure is incorrect:

```powershell
python -m src.schema --repair-headers
```

## Daily ingestion

```powershell
python -m src.gmail_ingestion --run
python -m src.main --static-pages-smoke-test
python -m src.main --job-upsert-smoke-test
```

Force Gmail replay only for controlled troubleshooting:

```powershell
python -m src.gmail_ingestion --run --force-reprocess
```

## Production enrichment

Preview daily work:

```powershell
python -m src.enrichment.production --dry-run --mode daily
```

Run the bounded daily cycle:

```powershell
python -m src.enrichment.production --run --mode daily
```

Run external-search fallback and lifecycle checks:

```powershell
python -m src.enrichment.production --run --mode weekly
```

Run a controlled backfill:

```powershell
python -m src.enrichment.production --run --mode backfill
```

Process one exact job:

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

Default limits:

| Mode | Direct | Company or ATS | External search | Lifecycle |
| --- | ---: | ---: | ---: | ---: |
| Daily | 10 | 10 | 0 | 0 |
| Weekly | 10 | 10 | 5 | 50 |
| Backfill | 15 | 15 | 5 | 50 |

A production cycle recovers stale queue work, runs permitted enrichment stages, re-scores jobs, refreshes Dashboard and Digest, writes health metrics, and records one `Runs` row.

## Lifecycle

Preview lifecycle work:

```powershell
python -m src.enrichment.lifecycle --dry-run
```

Run lifecycle checks directly:

```powershell
python -m src.enrichment.lifecycle --run --limit 50
```

A single timeout, HTTP 429, HTTP 5xx response, blocked page, parser failure, or untrusted result cannot close a role. Closure requires explicit authoritative evidence, expired authoritative `validThrough`, or repeated authoritative absence on later dates.

## GitHub Actions

`.github/workflows/daily-run.yml` performs ingestion and the main workbook refresh.

`.github/workflows/enrichment-run.yml` performs production enrichment:

* a successful daily workflow on `main` triggers `daily` mode
* Sunday scheduling triggers `weekly` mode
* manual dispatch supports `daily`, `weekly`, or `backfill`
* one concurrency group prevents overlapping enrichment runs
* the workflow timeout is 45 minutes

Pull requests are validated by `.github/workflows/pull-request-tests.yml`.

## Dashboard and Digest

Dashboard separates:

* verified immediate review
* verified strong fits
* high-potential enrichment pending
* partial evidence
* compensation unknown
* target-company watchlist
* enrichment failures
* recently closed roles

The production enrichment cycle appends current queue and lifecycle health metrics after each refresh.

## Documentation

* `docs/RUNBOOK.md`
* `docs/ENRICHMENT.md`
* `docs/TROUBLESHOOTING.md`
* `docs/operations_runbook.md`
* `docs/sprint_30_verified_scoring.md`
* `docs/sprint_31_enrichment_lifecycle.md`
* `docs/sprint_32_enrichment_production.md`

## Sprint implementation status

| Sprint | Status | Main addition |
| --- | --- | --- |
| 1 to 12 | Complete | Core ingestion, Sheets, scoring, dedupe, Dashboard, and workflow foundation |
| 13 to 19 | Complete | Schema safety, quarantine, quality gates, source audit, and scoring calibration |
| 20 to 25 | Complete | Weekly email, LinkedIn parsing, Gmail ledger, and extraction recovery |
| 26 | Complete | Potential priority, evidence completeness, and verified-fit separation |
| 27 | Complete | Enrichment queue, evidence audit trail, and direct-link extraction |
| 28 | Complete | Company career-site and ATS discovery |
| 29 | Complete | External-search fallback and safe matching |
| 30 | Complete | Company context and verified scoring |
| 31 | Complete | Enrichment retry and posting lifecycle |
| 32 | Complete | Production hardening, controlled rollout, monitoring, and documentation |
