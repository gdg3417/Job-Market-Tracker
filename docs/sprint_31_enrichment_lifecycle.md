# Sprint 31: Enrichment and Posting Lifecycle

## Goal

Prevent stale roles from remaining open indefinitely while keeping closure decisions conservative, auditable, and reversible.

## Posting states

The tracker supports these lifecycle states:

| State | Meaning |
| --- | --- |
| `open` | The posting is currently available or presumed available. |
| `not_seen_once` | A source-specific inventory pass missed the role once. |
| `likely_closed` | Supporting closure evidence exists, but the closure threshold has not been met. |
| `confirmed_closed` | Repeated or explicit authoritative evidence confirms closure. |
| `closed` | Supported terminal alias for imported or future lifecycle records. |
| `expired` | Structured `validThrough` evidence has passed. |
| `reopened` | A posting previously considered closed was found again. |

The existing `confirmed_closed` value remains supported to avoid rewriting historical rows.

## Closure evidence priority

Lifecycle decisions use the strongest available evidence first:

1. An employer or ATS page explicitly says the role is closed.
2. Structured `validThrough` is earlier than the check date.
3. An authoritative URL returns HTTP 404 or 410 repeatedly.
4. An authoritative ATS or company source no longer lists the posting repeatedly.
5. An authoritative posting consistently redirects to a generic careers or search page.
6. An aged Gmail-only role has repeated supporting absence without an authoritative page.

A single temporary failure, HTTP 429, HTTP 5xx response, timeout, blocked page, or unresolved search does not close a job.

## Conservative transitions

Authoritative absence follows a two-step transition:

```text
open
  -> likely_closed after the first authoritative absence
  -> confirmed_closed after the second distinct authoritative absence
```

An explicit closure statement can move directly to `confirmed_closed`. An expired `validThrough` value moves directly to `expired`.

Gmail-only unresolved roles remain visible. They can become `likely_closed` only after the configured age and repeated supporting absence. They do not become `confirmed_closed` from non-authoritative absence alone.

## Reopening

A specific authoritative posting rediscovered after `likely_closed`, `confirmed_closed`, `closed`, or `expired` moves to `reopened`. The closed date and lifecycle miss count are cleared. A closed enrichment queue row is returned to `pending` at the direct URL stage.

## Lifecycle audit fields

The Jobs worksheet adds these trailing fields:

- `lifecycle_last_checked_at`
- `lifecycle_next_check_at`
- `lifecycle_check_count`
- `lifecycle_miss_count`
- `lifecycle_last_evidence_key`
- `lifecycle_evidence_type`
- `lifecycle_evidence_url`
- `lifecycle_evidence_at`
- `lifecycle_reason`

Every distinct lifecycle observation is also written to `Enrichment_Evidence`. The observation hash prevents duplicate evidence rows and prevents an identical rerun from increasing closure counters.

## Retry schedule

The retry cadence after a completed enrichment attempt is:

| Attempt | Next delay |
| --- | --- |
| Initial attempt | Same run |
| Retry 1 | 1 day |
| Retry 2 | 3 days |
| Retry 3 | 7 days |
| Later retries | 7 days |

Maximum attempts depend on priority:

| Priority | Maximum attempts |
| --- | --- |
| High | 8 |
| Medium | 6 |
| Low | 4 |

Ambiguous matches remain manual review items and are not automatically retried. High-potential jobs therefore receive more automated recovery attempts than lower-priority jobs.

## Commands

Preview jobs due for lifecycle checking:

```powershell
python -m src.enrichment.lifecycle --dry-run
```

Run up to 50 lifecycle checks:

```powershell
python -m src.enrichment.lifecycle --run --limit 50
```

Check one job:

```powershell
python -m src.enrichment.lifecycle --run --job-key JOB_KEY --limit 1
```

The run migrates trailing worksheet headers before writing, records lifecycle evidence, updates Jobs and Enrichment_Queue, and appends a Runs record.

## Health metrics

Each lifecycle run calculates and records:

- Open verified jobs
- Open provisional jobs
- Enrichment backlog
- Retryable failures
- Ambiguous matches
- Jobs likely closed
- Jobs confirmed closed or expired
- Oldest pending enrichment age
- Average enrichment attempts
- Enrichment success rate

These values are included in the lifecycle Runs notes for Dashboard and workflow-summary use.

## Permanent regressions

Topgolf `Sr Manager, Strategic Planning` and Toyota North America `National Manager, Product` remain permanent regression cases.

Temporary retrieval failures or unresolved non-authoritative searches must leave both roles visible, high potential, and provisional. They may close only through the same recorded evidence thresholds as every other role.

## Sprint boundary

Sprint 31 provides the lifecycle engine, audit fields, retry policy, health metrics, command-line runner, and regression coverage. Sprint 32 remains responsible for scheduled production integration, controlled backfill, workflow concurrency, workbook presentation refinements, and rollout monitoring.
