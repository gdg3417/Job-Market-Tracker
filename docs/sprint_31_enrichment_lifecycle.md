# Sprint 31: Enrichment and Posting Lifecycle

## Goal

Prevent stale roles from remaining open indefinitely while keeping retry, closure, and reopening decisions conservative, auditable, and reversible.

## Posting states

| State | Meaning |
| --- | --- |
| `open` | The posting is currently available or presumed available. |
| `not_seen_once` | A source inventory pass missed the role once. |
| `likely_closed` | Supporting closure evidence exists, but the authoritative closure threshold has not been met. |
| `confirmed_closed` | Repeated or explicit authoritative evidence confirms closure. |
| `closed` | Supported terminal alias for imported or future lifecycle records. |
| `expired` | Authoritative structured `validThrough` evidence has passed. |
| `reopened` | A terminal or likely-closed posting was rediscovered through authoritative evidence. |

## Authority and posting-match rules

Lifecycle closure and reopening decisions may use only an employer posting, a supported ATS posting, or a previously verified enrichment URL with at least 80 match confidence.

Authority validation uses exact or suffix-safe hostname checks. A hostname that merely contains an ATS name, such as `greenhouse.io.example.com`, is not authoritative. The final redirect URL is revalidated before lifecycle evidence is accepted.

An authoritative host is not sufficient to prove that a page represents the tracked job. A parsed posting must also pass the existing title, company, location, seniority, role-family, and posting-ID match assessment before it can confirm the role is open, contribute structured expiration evidence, or reopen a terminal role. Ambiguous and rejected posting matches remain unresolved, and their `validThrough` values are ignored.

A stored enrichment URL is selected for lifecycle checking only when it belongs to a previously accepted match with at least 80 confidence and remains in a trusted enrichment state. Rejected, ambiguous, failed, and otherwise unverified enrichment URLs are ignored in favor of the canonical lead URL.

A job-board or other untrusted lead may redirect to an employer or ATS posting and become authoritative after the final page successfully matches the tracked job. A redirect to an ATS error, missing, closed, or generic page cannot create closure evidence unless the original requested URL was already authoritative.

General job boards, search engines, snippets, Gmail alerts, unrelated domains, and mismatched postings cannot confirm closure or reopening.

## Closure evidence priority

1. An authoritative employer or ATS page visibly and unambiguously says the role is closed.
2. Authoritative structured `validThrough` from a matching posting is earlier than the check date.
3. An authoritative URL returns HTTP 404 or 410 on two distinct, increasing check dates.
4. An authoritative posting consistently redirects to a generic careers or search page on two distinct, increasing check dates.
5. An authoritative inventory source explicitly reports the posting absent on two distinct, increasing check dates.
6. An aged Gmail-only role has repeated supporting absence without an authoritative page.

Closure language is evaluated only from visible page text. Script, style, template, metadata, and other hidden content are excluded. If a matching structured posting conflicts with visible closure language, the observation remains unresolved unless stronger evidence such as an expired matching `validThrough` value exists.

A successful HTTP 200 response that cannot be parsed as a specific matching job posting is unresolved evidence. It does not count as an authoritative absence or an authoritative open observation.

A single temporary failure, HTTP 429, HTTP 5xx response, timeout, blocked page, parser failure, mismatched posting, or unresolved search does not close a job.

## Separate evidence counters and temporal ordering

`lifecycle_miss_count` records only authoritative lifecycle absences. Weak Gmail and source-inventory absences use the existing `missed_count` field and can never satisfy the authoritative closure threshold.

`lifecycle_last_authoritative_miss_date` records the calendar date of the most recent authoritative absence. Additional authoritative misses from other URLs or source types on that same date are audited but do not increase `lifecycle_miss_count`. Older miss dates are also audited without advancing the counter or moving the recorded miss date backward.

An observation whose timestamp is older than `lifecycle_last_checked_at` is written to lifecycle evidence but cannot reverse or downgrade the newer job state, counters, audit pointer, or next-check schedule.

Before an observation can mutate Jobs or Enrichment_Queue, its prospective lifecycle evidence ID is compared with all existing `Enrichment_Evidence` rows. A previously recorded observation is skipped even when a different observation became the latest job-level evidence in between. This makes A-B-A observation sequences idempotent rather than only consecutive duplicates.

Jobs and queue synchronization are committed before the final `Enrichment_Evidence` row. If a partial Sheets write leaves persisted lifecycle fields without their deterministic evidence ID, the next run detects the incomplete transition even when the next-check date is in the future. It reconciles queue state without resetting an active retry cycle, restores a `lifecycle_recovery` audit row from the persisted Jobs fields, and performs no new network request for that recovery.

This prevents multiple weak misses followed by one authoritative 404, multiple same-day authoritative URLs, out-of-order backfill evidence, nonconsecutive duplicate evidence, and partial workbook writes from incorrectly changing or stranding lifecycle state.

## Conservative transitions

```text
open
  -> likely_closed after the first authoritative absence
  -> confirmed_closed after an authoritative absence on a later date
```

An explicit authoritative closure statement can move directly to `confirmed_closed`. An expired authoritative `validThrough` value from a matching posting moves directly to `expired`.

Gmail-only unresolved roles remain visible. They can become `likely_closed` only after the configured age and repeated supporting absence. They do not become `confirmed_closed` from non-authoritative evidence.

## Reopening

Normal ingestion updates `last_seen_date` but does not reopen `confirmed_closed`, `closed`, or `expired` jobs. This prevents stale or repeated Gmail alerts from reversing an authoritative closure.

A specific authoritative posting that passes match validation and is rediscovered after `likely_closed`, `confirmed_closed`, `closed`, or `expired` moves to `reopened`. The lifecycle miss counters, last authoritative miss date, and closed date are cleared. `Jobs.enrichment_status` is reset to `pending`.

Exactly one queue row owns the new enrichment cycle: the row whose deterministic enrichment ID corresponds to the job's current canonical URL. That row is reset to the direct URL stage with a fresh attempt budget, timestamps, prior match data, recovered fields, errors, and queue age. If the canonical row does not yet exist, it is created. Historical rows for older lead URLs remain closed.

The direct enrichment runner independently selects at most one queue row per `job_key`, preferring the current canonical enrichment ID even when an obsolete historical row is also due. It also revalidates the parent job against the current direct-link eligibility rules, so stale pending rows cannot process terminal, verified, excluded, low-priority, or otherwise ineligible jobs. These controls prevent duplicate or obsolete attempts from overwriting a stronger Jobs result.

## Lifecycle audit fields

The Jobs worksheet includes these trailing fields:

- `lifecycle_last_checked_at`
- `lifecycle_next_check_at`
- `lifecycle_check_count`
- `lifecycle_miss_count`
- `lifecycle_last_evidence_key`
- `lifecycle_evidence_type`
- `lifecycle_evidence_url`
- `lifecycle_evidence_at`
- `lifecycle_reason`
- `lifecycle_last_authoritative_miss_date`

Every distinct lifecycle observation is also written to `Enrichment_Evidence`. Equivalent observations on the same calendar day share one evidence key. Different same-day authoritative sources can create separate audit evidence, but the dedicated authoritative-miss date prevents them from advancing the closure counter more than once per day.

## Retry schedule and stage handoffs

Only transient failures use `retryable_failure`. The stage handoff statuses `not_found`, `ambiguous`, and `permanent_failure` are preserved so later company or ATS and external-search stages remain eligible.

The direct URL stage creates retry timestamps using this cadence:

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

When a transient failure reaches its priority limit, it becomes `permanent_failure`, which remains eligible for the next enrichment stage. Ambiguous matches remain manual review items and are not automatically retried.

## Commands

Preview lifecycle checks and retry timestamp changes without writes:

```powershell
python -m src.enrichment.lifecycle --dry-run
```

Run up to 50 lifecycle checks:

```powershell
python -m src.enrichment.lifecycle --run --limit 50
```

Check one job without modifying unrelated queue rows:

```powershell
python -m src.enrichment.lifecycle --run --job-key JOB_KEY --limit 1
```

The run migrates trailing worksheet headers before writing, records lifecycle evidence, updates Jobs and applicable Enrichment_Queue rows, and appends a Runs record.

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

These values are included in lifecycle Runs notes for Sprint 32 workflow summaries and workbook presentation.

## Permanent regressions

Topgolf `Sr Manager, Strategic Planning` and Toyota North America `National Manager, Product` remain permanent regression cases.

Temporary retrieval failures, parser failures, mismatched postings, hidden closure labels, untrusted redirects, rejected enrichment URLs, stale observations, duplicate evidence, obsolete queue rows, ineligible parent jobs, partial workbook writes, and unresolved searches must leave both roles visible, high potential, and provisional. They may close only through the same recorded authoritative thresholds as every other role.

## Sprint boundary

Sprint 31 provides the lifecycle engine, strict authority and posting-match validation, source-trust rules, temporal ordering, evidence-idempotency and partial-write recovery safeguards, canonical queue ownership, parent-job eligibility gates, retry policy, queue synchronization, health metrics, command-line runner, and regression coverage.

Sprint 32 remains responsible for scheduled production integration, controlled backfill, workflow concurrency, Dashboard presentation of the lifecycle health metrics, recent-closure display refinements for imported terminal aliases, manual lifecycle override tooling, and rollout monitoring.
