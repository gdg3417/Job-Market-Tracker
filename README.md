# Job Market Tracker

A zero added cost Python, GitHub Actions, Google Sheets, and Google Apps Script job market tracker for commercial leadership, business operations, revenue strategy, business insights, corporate strategy, product line management, category management, Chief of Staff, and P&L pathway roles.

The tracker is intentionally not a generic finance job scraper. It monitors roles that could improve compensation, benefits, commute, flexibility, executive exposure, operating ownership, team leadership, and long-term path toward VP, SVP, business unit leadership, or P&L ownership.

## Current status

Sprints 1 through 27 are implemented in code.

The current system supports:

1. Google Sheets writes
2. Greenhouse and Lever ingestion
3. Static company career page ingestion
4. Gmail alert ingestion
5. Dedupe and lifecycle handling
6. Dashboard and Digest refresh
7. Workbook schema validation
8. Gmail quarantine handling
9. Final data quality gates
10. A safer daily GitHub Actions workflow
11. Source configuration auditing
12. Focused scoring for passive job monitoring
13. A plain-English executive Dashboard
14. A Google Apps Script weekly email digest
15. LinkedIn multi-job digest card parsing with stable posting IDs
16. Sparse Gmail high-signal title review routing without score inflation
17. Paginated, ledger-backed Gmail ingestion with per-message retries and idempotent rejection handling
18. Potential-priority, evidence-completeness, and verified-score states
19. An auditable enrichment queue with controlled direct-link extraction

Sprint 20 redesigns the Dashboard so it is an action-oriented executive summary instead of a fragile formula page. It also adds a bound Apps Script weekly digest email that can be scheduled for Monday around 8:00 AM Central.

Sprint 21 parses LinkedIn digest emails as individual job cards. Each accepted card retains its own title, company, location, canonical posting URL, and stable `linkedin-<job_id>` source ID. LinkedIn utility links are ignored, and malformed cards are rejected individually without discarding valid cards from the same email.

Sprint 22 flags sparse Gmail records with strategically relevant management-level titles for human review. It preserves evidence-based scores, adds a dedicated Digest and Dashboard section, updates the weekly email, and provides a command to re-score existing open Gmail jobs.

Sprint 23 adds the `Gmail_Messages` ledger, paginated Gmail listing, retryable message processing, force reprocessing, idempotent `Rejected_Jobs` writes, backlog metrics, and a Central-date workflow completion lock. The two UTC schedules remain, but only the first successful run for a Central calendar date records completion.

Sprint 26 separates potential priority from verified fit. Missing salary or description details reduce evidence completeness instead of automatically lowering a promising role into a completed low-fit recommendation.

Sprint 27 adds `Enrichment_Queue` and `Enrichment_Evidence`, controlled direct-link fetching, JSON-LD extraction, match-confidence validation, and safe evidence merging. It does not yet add broad web search, company career-site discovery, or production scheduling.

## Repo structure

```text
job-market-tracker/
  apps_script/
    weekly_digest_email.gs
  config/
    potential_priority_rules.yml
    scoring_rules.yml
    sparse_gmail_review.yml
    target_profile.yml
  docs/
    operations_runbook.md
    sprint_20_weekly_email_dashboard.md
    sprint_22_sparse_gmail_review.md
    sprint_23_gmail_ingestion.md
    sprint_27_direct_link_enrichment.md
  src/
    daily_run_gate.py
    dashboard.py
    enrichment/
      extractors.py
      fetcher.py
      json_ld.py
      matcher.py
      merge.py
      models.py
      queue.py
      run.py
    gmail_ingestion.py
    potential_priority.py
    rescore_jobs.py
    scoring.py
    sources/
      eml.py
      gmail_alerts.py
      linkedin_digest.py
  tests/
    enrichment/
    fixtures/
      linkedin_topgolf.eml
      linkedin_toyota.eml
    test_daily_run_gate.py
    test_dashboard.py
    test_gmail_alerts.py
    test_gmail_ingestion.py
    test_linkedin_digest.py
    test_rescore_jobs.py
    test_scoring.py
    test_weekly_digest_email.py
```

Other source, test, config, and workflow files remain in their existing locations.

## Workbook structure

The canonical workbook schema is managed in `src/schema.py`.

Required tabs:

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

`Rejected_Jobs` captures records blocked by final quality gates. It is not a staging tab for good jobs.

`Gmail_Messages` is the message processing ledger. It records each Gmail message ID, processing status, attempt count, parsed and accepted counts, errors, and processing timestamps.

`Enrichment_Queue` records one deterministic direct-link work item for each eligible job and lead URL. `Enrichment_Evidence` stores parsed evidence, match confidence, acceptance status, and a content hash without storing full raw HTML.

## Local setup

Run these commands from PowerShell.

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

If script execution is blocked in PowerShell, run this once for the current user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Core validation commands

Run these before trusting an unattended daily workflow run:

```powershell
pytest
python -m src.gmail_ingestion --ensure-ledger
python -m src.schema --migrate
python -m src.schema --validate
python -m src.source_audit
python -m src.gmail_ingestion --run
python -m src.main --static-pages-smoke-test
python -m src.dashboard
```

Use this command only when the workbook headers or timezone need repair:

```powershell
python -m src.schema --repair-headers
```

Use force reprocessing only for controlled debugging or a deliberate replay:

```powershell
python -m src.gmail_ingestion --run --force-reprocess
```

## LinkedIn Gmail digest parsing

LinkedIn digest emails are parsed from both available MIME alternatives. When the same LinkedIn job ID appears in both, the parser retains the more complete valid card. Plain text or HTML can be used independently when only one contains direct postings.

Each direct LinkedIn posting is normalized to:

```text
https://www.linkedin.com/jobs/view/<job_id>
```

The provider source ID is:

```text
linkedin-<job_id>
```

This keeps the same posting stable when it appears in multiple alert emails. Search pages, Premium links, alert-management links, unsubscribe links, help pages, and LinkedIn navigation links do not create job records.

Sanitized regression fixtures are stored under `tests/fixtures/`.

## Sparse Gmail review routing

Sprint 22 identifies Gmail records whose descriptions contain only extraction metadata and whose compensation and work model are unknown. A configurable high-signal management title receives these score explanation markers:

```text
manual_review=true
review_reason=sparse_gmail_high_signal_title
```

The numerical score and alert tier are not increased. Recent qualifying roles appear in `High-signal titles needing review` even when the score is below 60.

Re-score existing open Gmail records and refresh Dashboard and Digest with:

```powershell
python -m src.rescore_jobs
```

See `docs/sprint_22_sparse_gmail_review.md` for criteria and operating details.

## Gmail processing ledger

Successful and no-job messages are skipped on later runs. Retryable failures are attempted again. Permanent failures remain recorded and are skipped unless force reprocessing is used.

Supported statuses:

```text
success
no_jobs
retryable_failure
permanent_failure
```

A message is marked complete only after its accepted jobs, rejected records, and ledger row have been written. Rejected rows are keyed by `rejected_id`, so retries do not append duplicate quarantine rows.

See `docs/sprint_23_gmail_ingestion.md` for backlog and recovery procedures.

## Direct-link enrichment

Preview eligible jobs without workbook writes:

```powershell
python -m src.enrichment.run --dry-run
```

Create or migrate the enrichment tabs, enqueue eligible jobs, and process up to ten direct URLs:

```powershell
python -m src.enrichment.run --run --limit 10
```

Process one existing job:

```powershell
python -m src.enrichment.run --run --job-key "<job_key>" --limit 1
```

The direct-link fetcher validates every redirect destination, blocks private and local network targets, limits response size, and records blocked or missing direct links as `not_found` so later company and ATS discovery can continue. See `docs/sprint_27_direct_link_enrichment.md`.

## Weekly email digest

Sprint 20 adds `apps_script/weekly_digest_email.gs` for a bound Google Apps Script weekly digest.

Setup is documented in:

```text
docs/sprint_20_weekly_email_dashboard.md
```

The weekly email reads the `Digest` tab, sends through Apps Script, and can be scheduled for Monday around 8:00 AM Central. It should not be run from the daily GitHub Actions workflow. Sprint 22 adds the high-signal Gmail review queue to the email.

## Main commands

```powershell
python -m src.main --dry-run
python -m src.main --sheets-smoke-test
python -m src.main --greenhouse-smoke-test
python -m src.main --lever-smoke-test
python -m src.main --job-upsert-smoke-test
python -m src.gmail_ingestion --ensure-ledger
python -m src.gmail_ingestion --run
python -m src.main --static-pages-smoke-test
python -m src.rescore_jobs
python -m src.source_audit
python -m src.source_audit --apply-recommendations
python -m src.enrichment.run --dry-run
python -m src.enrichment.run --run --limit 10
python -m src.dashboard
python -m src.workflow_validation
```

## GitHub Actions

The daily workflow is `.github/workflows/daily-run.yml`.

It supports manual runs with `workflow_dispatch` and two scheduled UTC invocations around 06:30 AM Central. Scheduled runs use a successful `daily_workflow_completion` record in `Runs` as the Central-date lock. A delayed first invocation still runs. The second invocation skips only after the first invocation completes successfully. Manual dispatch always bypasses the date lock.

Manual dispatch also exposes a `force_reprocess` input for controlled Gmail replay.

Sprint 27 direct-link enrichment is intentionally not part of the scheduled daily workflow. Production integration remains planned for Sprint 32.

Pull requests are validated by `.github/workflows/pull-request-tests.yml`, which compiles Python sources and runs the full pytest suite.

## Sprint implementation status

| Sprint | Status | Main addition |
| --- | --- | --- |
| Sprint 1 | Complete | Local Python scaffold and dry-run |
| Sprint 2 | Complete | Google Sheets smoke test and `Runs` write |
| Sprint 3 | Complete | Config and scoring foundation |
| Sprint 4 | Complete | Target profile and search configuration cleanup |
| Sprint 5 | Complete | Greenhouse source support |
| Sprint 6 | Complete | Lever source support |
| Sprint 7 | Complete | Job upsert and dedupe into `Jobs` and `Job_Sources` |
| Sprint 8 | Complete | Lifecycle handling for missing and closed jobs |
| Sprint 9 | Complete | Gmail alert ingestion |
| Sprint 10 | Complete | Static company career page ingestion |
| Sprint 11 | Complete | Dashboard and Digest generation |
| Sprint 12 | Complete | GitHub Actions daily workflow foundation |
| Sprint 13 | Complete | Workbook schema validation and repair |
| Sprint 14 | Complete | Gmail quarantine and cleanup handling |
| Sprint 15 | Complete | Final data quality gates and `Rejected_Jobs` capture |
| Sprint 16 | Complete | Workflow automation readiness and safer daily run ordering |
| Sprint 17 | Complete | Documentation and runbook cleanup |
| Sprint 18 | Complete | Source configuration audit, source quality fields, and ingestion mode recommendations |
| Sprint 19 | Complete | Scoring tuning and focused Digest sections for weekly review usefulness |
| Sprint 20 | Complete | Plain-English Dashboard and Apps Script weekly email digest |
| Sprint 21 | Complete | LinkedIn multi-job digest card parsing and stable posting IDs |
| Sprint 22 | Complete | Sparse Gmail high-signal title review routing and re-score command |
| Sprint 23 | Complete | Gmail ledger, pagination, retries, idempotent rejection writes, backlog metrics, and daily completion lock |
| Sprint 24 | Complete | Recover LinkedIn lead cards from malformed or sparse alerts |
| Sprint 25 | Complete | Preserve plain-text LinkedIn card ordering |
| Sprint 26 | Complete | Separate potential priority, evidence completeness, and verified fit |
| Sprint 27 | Complete | Enrichment queue, evidence audit trail, and direct-link extraction |
