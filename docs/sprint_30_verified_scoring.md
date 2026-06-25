# Sprint 30: Company Context and Verified Scoring

## Goal

Produce a verified score only when the tracker has enough evidence to support the recommendation. Missing evidence remains unknown instead of becoming negative evidence.

## Scoring states

| State | Meaning | Recommended action |
| --- | --- | --- |
| `provisional` | The title, company, and location may be promising, but the posting evidence is sparse. | Enrich or review. |
| `partially_verified` | Some posting evidence was recovered, but one or more verification requirements remain unmet. | Review recovered evidence. |
| `verified` | The posting has a credible identity, meaningful description, location or remote designation, authoritative source, and sufficient match confidence. | Apply, review, or pass based on the verified tier. |
| `excluded` | A configured hard exclusion applies. | Do not pursue. |

## Verification requirements

A job is verified only when all of the following are present:

1. Credible title.
2. Credible company.
3. Credible location or remote designation.
4. Meaningful job description.
5. Authoritative employer or ATS posting URL, or a controlled direct source.
6. Accepted match confidence when alert, enrichment, or external-search evidence supplied the match.
7. Evidence completeness at or above the configured threshold.

Unrecognized or search-derived sources cannot become verified from a safe URL alone. They require accepted match confidence and an employer, configured company, or known ATS domain.

## Missing compensation

Salary is not required for verification. When compensation is missing:

1. `comp_score` remains zero in the raw score because no compensation evidence exists.
2. The verified score is normalized across the other configured categories.
3. `compensation_status=unknown` and `verified_score_basis=normalized_without_compensation` are recorded.
4. The role remains eligible for the `Needs salary research` section.

Known compensation below the configured floor is not normalized and remains negative evidence.

## Company context

Company context is resolved from `Config_Companies` and `Target_Companies` by:

1. Company name.
2. Canonical company name.
3. Parent company.
4. Explicit company aliases.

Company context can provide industry scoring and a capped company preference boost. Only structured industry, ownership, and company-size fields can award industry points. URLs, notes, P&L rationale, and other context fields cannot create job-level or industry score points.

P&L, growth, executive exposure, operating cadence, fit, and penalty keywords are evaluated from role fields and the posting description. Company names, locations, URLs, and company notes are excluded from those keyword calculations.

## Audit fields

The score explanation records:

- `authoritative_source`
- `match_confidence_status`
- `verification_gaps`
- `verified_total_score`
- `verified_alert_tier`
- `verified_score_basis`
- `recommended_action`
- `compensation_status` when compensation is unknown

## Rescore commands

The historical command remains backward compatible. It re-scores open Gmail jobs and refreshes Dashboard and Digest:

```powershell
python -m src.rescore_jobs
```

Run the legacy Gmail rescore without the Dashboard and Digest refresh:

```powershell
python -m src.rescore_jobs --no-refresh
```

Preview all open jobs without writing:

```powershell
python -m src.rescore_jobs --all-open --dry-run
```

Rescore all open jobs and refresh Dashboard and Digest:

```powershell
python -m src.rescore_jobs --all-open --refresh-dashboard
```

Rescore provisional jobs and refresh Dashboard and Digest:

```powershell
python -m src.rescore_jobs --provisional-only --refresh-dashboard
```

Rescore one job:

```powershell
python -m src.rescore_jobs --job-key JOB_KEY --refresh-dashboard
```

Rescore one company or configured company alias:

```powershell
python -m src.rescore_jobs --company "Toyota North America" --refresh-dashboard
```

Rescore verified jobs for calibration:

```powershell
python -m src.rescore_jobs --verified-only --dry-run
```

## Permanent regressions

Topgolf `Sr Manager, Strategic Planning` and Toyota North America `National Manager, Product` must remain visible as high-potential provisional roles until authoritative evidence is recovered. Neither may display a verified low-fit recommendation based only on sparse alert content.

## Deferred

Sprint 31 remains responsible for retries, expiry, closure evidence, stale-job handling, and reopening logic. Sprint 32 remains responsible for production scheduling, controlled backfill, monitoring, and rollout documentation.
