# Sprint 22: Sparse Gmail alert review routing

## Purpose

Sprint 22 prevents strategically relevant Gmail alert postings from disappearing when the alert contains only title, company, location, and a direct posting URL.

The numerical score continues to represent only evidence available in the record. Sprint 22 does not award assumed P&L ownership, compensation, executive exposure, operating cadence, or industry points.

## Sparse Gmail criteria

A record is sparse when all of the following are true:

1. `source_primary` is `gmail_alert`.
2. The description is blank or contains only the generic Gmail extraction metadata.
3. Salary and total compensation are missing.
4. Remote status or work model is unknown or incomplete.

A sparse record is routed for review only when its title contains both:

1. A configured high-signal role phrase.
2. A configured management-level seniority phrase.

The configurable phrases are stored in `config/sparse_gmail_review.yml` and loaded with the main scoring rules.

## Review marker

Qualifying records retain their calculated score and tier. The scoring explanation adds:

```text
manual_review=true
review_reason=sparse_gmail_high_signal_title
```

Hard-excluded records do not receive this marker.

## Digest behavior

The Digest includes a section named:

```text
High-signal titles needing review
```

The section appears after `Strong fit`, includes recent qualifying roles even below 60 points, and is capped at 15 records. The Dashboard and weekly Apps Script email include the same queue.

## Re-score existing open Gmail jobs

Run:

```powershell
python -m src.rescore_jobs
```

This command:

1. Re-scores open and reopened Gmail jobs.
2. Updates their existing `Jobs` rows.
3. Refreshes Dashboard and Digest.
4. Appends a Sprint 22 record to `Runs`.

Optional flags:

```powershell
python -m src.rescore_jobs --no-refresh
python -m src.rescore_jobs --no-run-log
```

## Validation

Run:

```powershell
pytest
python -m src.schema --validate
```

The Gmail backlog remains blocked until Sprint 23. Sprint 22 does not run a production Gmail backfill.
