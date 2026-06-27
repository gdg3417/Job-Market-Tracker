# Sprint 35: Priority ATS Connectors and Source Reliability

## Purpose

Sprint 35 formalizes platform-aware ATS connector behavior and adds source reliability state so priority sources are managed separately from generic career-page scraping.

The sprint does not attempt to support every ATS equally. It inventories active priority configurations and treats existing structured adapters for Greenhouse, Lever, Ashby, and SmartRecruiters as the first connector scope. Platforms such as Workday, iCIMS, SuccessFactors, Oracle Recruiting, Jobvite, and Phenom remain recognized and configured for authoritative routing, but they are not treated as safe structured connectors unless a stable endpoint is available.

## Connector contract

All connector observations normalize to this result model:

- `success`
- `no_matching_jobs`
- `posting_not_found`
- `unauthorized`
- `blocked`
- `rate_limited`
- `temporary_server_failure`
- `parser_failure`
- `invalid_configuration`
- `unsupported_platform`

A connector result also records platform, company, source URL, request count, pages fetched, response time, rate-limit status, normalized jobs, and structured error detail.

Normalized jobs include requisition ID, canonical posting URL, title, company, location, posting dates, employment type, work arrangement, salary range, currency, description, department, current posting status, and source metadata when available.

## Selected connector scope

Structured connector scope is currently:

- Greenhouse
- Lever
- Ashby
- SmartRecruiters

These platforms have stable board or posting APIs already present in the codebase and can safely return structured job data without unrestricted crawling.

Configured-only scope is currently:

- Workday
- iCIMS
- SuccessFactors
- Oracle Recruiting
- Jobvite
- Phenom

Configured-only means the platform is recognized for authoritative routing and manual review, but broad generic scraping is not promoted to a structured connector.

## Platform inventory

Run the inventory report with:

```powershell
python -m src.connectors.inventory --dry-run
```

The report ranks platforms by:

- Priority company count
- Tier 1 and Tier 2 company count
- Unresolved high-potential jobs
- Active configuration count
- Invalid configurations
- Watch or paused sources
- Existing platform health
- Expected implementation value

The output also lists the selected structured connector scope.

## Source reliability state

Sprint 35 adds the `Source_Health` worksheet. One row represents the current health state for a company, platform, and source URL.

Tracked fields include:

- Last attempted
- Last successful
- Consecutive failures
- Attempt count
- Success count
- Failure count
- Success rate
- Median response time
- Last error category
- Last HTTP status
- Jobs found
- Jobs accepted
- Empty-success count
- Configuration-valid flag
- Rate-limit events
- Source state
- Pause or manual-review reason

## Source control states

Supported states are:

- `healthy`
- `watch`
- `temporarily_paused`
- `manual_review_required`
- `disabled`

Automatic controls are conservative. One timeout, one blocked response, one parser failure, or one rate-limit response will not pause or disable a source.

Default behavior:

- One temporary failure leaves the source healthy.
- Two consecutive failures move the source to watch.
- Three consecutive failures move the source to temporarily paused.
- Invalid configuration or unsupported platform moves the source to manual review required.
- Successful observations reset consecutive failures and clear temporary pause reasons.
- Disabled is reserved for explicit manual or configuration action.

## Resolver and lifecycle compatibility

The Sprint 34 resolver already discovers candidates through configured ATS boards via the shared ATS discovery path. Sprint 35 wraps this behavior with a normalized connector contract and inventory layer so later production runs can compare platform reliability without changing match thresholds.

Connector failures produce source reliability evidence only. They must not close jobs. Lifecycle closure remains governed by the conservative Sprint 31 and Sprint 38 rules.

## Troubleshooting

Use `Source_Health` first when a platform appears degraded.

Common interpretations:

- `invalid_configuration`: fix `Config_Companies` fields such as ATS platform, board token, company ID, or career search URL.
- `unsupported_platform`: the platform is recognized or configured but does not yet have a safe structured connector.
- `temporary_server_failure`: retry later. Do not manually replace a posting URL solely from this error.
- `rate_limited`: reduce bounded runs and retry later.
- `no_matching_jobs`: the connector returned successfully but found no current jobs. Repeated empty success moves the source to watch, not failure.
- `temporarily_paused`: inspect the last three failures before reactivating.

## Production validation

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
git fetch origin

git switch codex/sprint-35-ats-connectors-source-reliability
git pull --ff-only origin codex/sprint-35-ats-connectors-source-reliability

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation

python -m src.connectors.inventory --dry-run
python -m src.resolution.run --dry-run --limit 10
python -m src.enrichment.pipeline --dry-run --company-limit 10 --external-limit 0
python -m src.enrichment.production --dry-run --mode daily --resolution-limit 5 --company-limit 5 --external-limit 0
python -m src.enrichment.production --run --mode daily --resolution-limit 5 --company-limit 5 --direct-limit 5 --external-limit 0 --lifecycle-limit 0
python -m src.schema --validate
```

Review in the workbook:

1. `Source_Health` exists with canonical headers.
2. The inventory report ranks priority platforms and identifies structured versus configured-only scope.
3. Greenhouse, Lever, Ashby, and SmartRecruiters are treated as structured connector platforms.
4. Configured-only platforms are not treated as generic posting scrapers.
5. No source is disabled from a single transient failure.
6. Repeated connector observations update one current source-health row per source.
7. `Posting_Resolution`, `Resolution_Candidates`, `Enrichment_Evidence`, `Job_Sources`, `Dashboard`, `Digest`, and `Runs` do not duplicate rows from a rerun.
8. No connector failure marks a job closed.

## Intentionally deferred

- Full structured implementations for Workday, iCIMS, SuccessFactors, Oracle Recruiting, Jobvite, and Phenom.
- Human review and application workflow from Sprint 36.
- Compensation, work model, benefits, and commute intelligence from Sprint 37.
- Lifecycle maturity, regression precision and recall, readiness gates, and alerts from Sprint 38.
