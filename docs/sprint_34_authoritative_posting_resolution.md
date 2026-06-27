# Sprint 34: Authoritative Posting Resolution

## Purpose

Sprint 34 adds a dedicated resolver between sparse lead intake and evidence enrichment. It converts LinkedIn, Gmail, job-board, and partial source records into a canonical employer or applicant tracking system posting when the available evidence is strong enough.

The resolver does not weaken the existing match gate. A title match by itself cannot produce an authoritative resolution. Company identity, title, and source authority must all pass their individual gates. Ambiguous and probable candidates remain reviewable and do not merge evidence.

## Canonical workbook tables

### `Posting_Resolution`

One current row per job. This table stores:

* resolution state
* selected authoritative URL
* detected platform and stable identifier
* candidate count
* total confidence and all score components
* attempt and resolution timestamps
* normalized blocker and detailed error text
* durable manual override fields

The deterministic `resolution_id` is based on `job_key`. Reruns update the current row rather than appending duplicate current-state rows.

### `Resolution_Candidates`

One row per unique job and canonical candidate URL. It preserves:

* discovery order and method, with pipe-delimited paths when the same URL is found through more than one method
* original observed URL
* canonical URL
* platform and identifiers
* source title, company, location, posting date, and description excerpt
* all visible score components
* acceptance or rejection state
* rejection reason and observation timestamps

A candidate discovered through multiple paths is stored once, while the combined discovery paths remain visible. The original source records in `Job_Sources` remain unchanged. When a candidate is accepted, an additional `authoritative_resolution` source row records the canonical posting without deleting the observed lead URL.

## Candidate discovery order

The resolver evaluates candidates in this order:

1. Existing accepted authoritative evidence
2. Direct URL resolution, including safe redirect handling
3. Configured employer career search context
4. Configured ATS board discovery
5. Known ATS URL patterns already present in source records
6. Controlled external search fallback
7. Manual override

The implementation does not use unrestricted crawling. Search and page budgets are configured in `config/posting_resolution.yml`.

## URL rules

The resolver:

* unwraps common email-safe, LinkedIn outbound, search redirect, and tracking links
* resolves bounded HTTP redirect chains through the existing safe fetcher
* rejects private, local, credential-bearing, and unsupported destinations
* removes tracking parameters such as `utm_*`, `ref`, `source`, `gh_src`, and `lever-source`
* removes fragments and normalizes scheme, host, path, and query ordering
* preserves the original observed URL separately from the canonical URL

Blocked or expired links produce structured states and error text. They do not close a job.

## ATS recognition

The resolver identifies common ATS hosts and stable identifiers for:

* Workday
* Greenhouse
* Lever
* iCIMS
* SmartRecruiters
* SuccessFactors
* Oracle Recruiting
* Jobvite
* Phenom

Custom-domain ATS sites use the configured ATS platform and career domain when host-only recognition is not possible. Sprint 35 remains responsible for broader structured platform connectors and source reliability controls.

## Match confidence

All components are stored on both the selected resolution and candidate rows.

| Component | Weight |
| --- | ---: |
| Company match | 25 percent |
| Title match | 25 percent |
| Location match | 10 percent |
| Requisition ID match | 20 percent |
| Description similarity | 8 percent |
| Posting-date consistency | 4 percent |
| Source-domain authority | 5 percent |
| ATS identifier consistency | 3 percent |

An exact requisition ID match is highly weighted and sets a confidence floor when the company gate also passes. A strong exact company, title, location, and authoritative-domain match receives a bounded consistency bonus even when the lead did not contain an employer requisition ID.

Default thresholds:

* authoritative: 82
* probable: 70
* ambiguity margin: 5
* minimum company match: 75
* minimum title match: 70

A candidate cannot be authoritative unless company, title, and source-domain authority gates all pass. Title similarity alone is never sufficient.

## Resolution states

| State | Meaning | Evidence merge allowed |
| --- | --- | --- |
| `resolved_authoritative` | One candidate passed all gates and the authoritative threshold | Yes |
| `resolved_probable` | One plausible candidate is below the authoritative threshold | No |
| `ambiguous` | Multiple plausible candidates are within the ambiguity margin | No |
| `not_found` | No candidate passed the probable threshold | No |
| `blocked` | A source prevented safe retrieval | No |
| `unsupported` | No supported posting-level path exists | No |
| `manual_override` | A reviewer-provided URL passed validation | Yes |
| `retryable_failure` | A temporary network or source failure should be retried | No |

`resolved_probable`, `ambiguous`, `blocked`, and `unsupported` require manual review. `retryable_failure` maps to the existing retry blocker.

## Manual override procedure

Edit the existing row in `Posting_Resolution` for the job:

1. Enter the employer or ATS posting in `manual_authoritative_url`.
2. Set `manual_resolution_decision` to `accept` or `replace`.
3. Enter `manual_reviewer`, `manual_review_date`, and `manual_notes`.
4. Run the resolver for the exact job key.

The URL must still resolve to a recognizable job posting and pass company, title, and source-authority validation. Automated candidates remain in `Resolution_Candidates`. Later automated runs do not erase the manual fields.

To remove an override, set `manual_resolution_decision` to `remove` and rerun the exact job. The current manual fields are cleared, while a `manual_removed` candidate audit row remains. To prevent an automated candidate from merging while retaining it for review, set the decision to `reject_automated`.

## Commands

Preview eligible work without writes:

```powershell
python -m src.resolution.run --dry-run --limit 10
```

Run a bounded resolver cycle without external search:

```powershell
python -m src.resolution.run --run --limit 10 --no-web-search
```

Run controlled external search fallback:

```powershell
python -m src.resolution.run --run --limit 10
```

Process one exact job:

```powershell
python -m src.resolution.run --run --limit 1 --job-key "<job_key>"
```

The production enrichment command now runs authoritative resolution before the existing direct, company or ATS, and external-search stages:

```powershell
python -m src.enrichment.production --run --mode daily
```

Default resolution limits are 10 for daily, 15 for weekly, and 20 for backfill. Use `--resolution-limit` for a controlled override.

## Observability integration

Sprint 33 health now reads `Posting_Resolution`.

* A resolver attempt counts as enrichment attempted.
* `resolved_authoritative` and validated `manual_override` count as authoritative posting found.
* Probable, ambiguous, blocked, unsupported, not-found, and retryable states map to normalized blocker reasons.
* Resolver attempts and failures are included in source-health metrics from production run notes.
* The production GitHub Actions summary shows attempts, successful resolutions, candidates, and manual interventions.

An empty enrichment queue still cannot make verification health appear healthy when priority jobs remain unresolved.

## Topgolf and Toyota regressions

Permanent regression keys:

* Topgolf `Sr Manager, Strategic Planning`: `job-a4f80647216b`
* Toyota North America `National Manager, Product`: `job-4988871ff583`

Tests verify that both can resolve through general configured ATS candidate logic without job duplication. When the configured Phenom path does not expose a posting-level adapter, the resolver records a stable `unsupported` state and detailed reason rather than accepting the career search landing page.

## Production validation

Run from a new PowerShell window after the pull request is ready for validation:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
git fetch origin

git switch codex/sprint-34-authoritative-posting-resolution
git pull --ff-only origin codex/sprint-34-authoritative-posting-resolution

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation

python -m src.resolution.run --dry-run --limit 10
python -m src.resolution.run --run --limit 5 --no-web-search

python -m src.resolution.run --run --limit 1 --job-key "job-a4f80647216b"
python -m src.resolution.run --run --limit 1 --job-key "job-4988871ff583"

python -m src.verification_health --run --run-id "sprint34_controlled_validation"
python -m src.schema --validate
```

Review these workbook checks:

1. `Posting_Resolution` contains no more than one current row for each tested `job_key`.
2. `Resolution_Candidates` shows the observed and canonical URLs, score components, state, and rejection reason.
3. Topgolf and Toyota either have a validated canonical posting or a precise stable blocker.
4. Low-confidence and ambiguous candidates did not overwrite `Jobs.canonical_url` or accepted evidence.
5. Existing LinkedIn or Gmail URLs remain in `Job_Sources`.
6. Accepted canonical postings added no duplicate `Jobs` rows.
7. A second exact-job rerun did not duplicate candidate, evidence, source, or resolution rows.
8. The Sprint 33 Dashboard funnel and blocker sections reflect the resolver state.
9. Dashboard and Digest rows remain functional and are not duplicated.

## Rollback

The resolver is additive. To suspend it without deleting audit data, run production with `--resolution-limit 0`. Existing enrichment stages continue to operate. A code rollback can remove the resolver call while leaving the two new worksheets in place. Extra canonical worksheets are harmless, but schema validation should use the matching code version.

Do not delete `Resolution_Candidates` when investigating a bad match. It is the audit record needed to explain and correct the decision.

## Intentionally deferred

Sprint 34 does not implement:

* broad structured ATS connectors or platform inventory
* source reliability state and automatic source pausing
* the full human review and application workflow
* compensation, benefits, work-model, or commute intelligence
* priority-based lifecycle maturity and final production-readiness gates

Those remain Sprints 35 through 38.
