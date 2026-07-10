# Sprint 44: Weekly Tracker Value Dashboard

## Objective

Sprint 44 adds a generated `Weekly_Value` worksheet that shows tracker volume, human review progress, application activity, follow-up workload, fit quality, and noise removal by calendar week.

`Jobs` remains the canonical source-of-truth table. Sprint 44 does not add, remove, reorder, or rewrite canonical `Jobs` columns.

## Weekly structure

Each row represents a Monday through Sunday week.

The current week is calculated through the refresh date. Completed weeks retain their recorded values so later status changes do not rewrite long-term history. The immediately prior week is recalculated to capture edits made shortly after the week closes.

The initial refresh reconstructs up to 12 weeks from durable dates already present in the workbook. The backfill length is configurable.

## Metrics

The generated worksheet includes:

1. Jobs Added
2. Jobs Reviewed
3. Jobs Dismissed
4. Jobs Applied
5. Jobs Moved to Active Status
6. Jobs Still Not Reviewed Yet
7. Follow-ups Due
8. Outstanding Active Roles
9. Strong Fit Jobs
10. Stretch Fit Jobs
11. Auto-Rejected Jobs
12. Blocked Company Rejects
13. Too-Senior Rejects or Penalties
14. Review Completion Rate
15. Actionable Conversion Rate
16. Dismissal Rate
17. Backlog Change
18. Signal Quality
19. Noise Removed
20. Notes

## Metric definitions

### Jobs Added

Counts canonical `Jobs` rows whose `first_seen_date` falls within the week.

### Jobs Reviewed

Counts rows whose first durable review transition date, using `reviewed_date` with `application_date` as a fallback, falls within the week.

### Jobs Dismissed

Counts reviewed rows whose current manual decision is Dismissed, Rejected, Closed, Withdrawn, Not Interested, or an equivalent controlled value.

### Jobs Applied

Counts rows whose `application_date` falls within the week.

### Jobs Moved to Active Status

Counts currently outstanding active roles whose most recent durable application or review update falls within the week.

### Jobs Still Not Reviewed Yet

Counts nonterminal jobs that existed by the end of the week and did not yet have a durable review transition. Automated hard exclusions are excluded from the manual backlog.

### Follow-ups Due and Outstanding Active Roles

Reuses Sprint 43 follow-up evaluation. The weekly dashboard does not create separate aging logic.

### Strong Fit Jobs

Counts jobs added during the week that meet the existing verified strong-fit threshold or alert tier. Blocked companies, too-senior roles, and Director stretch roles are excluded from this count.

### Stretch Fit Jobs

Counts jobs added during the week that are classified as Director stretch opportunities by role level or Sprint 42 seniority tags.

### Auto-Rejected Jobs

Counts dated `Rejected_Jobs` rows plus canonical `Jobs` rows added during the week that were hard excluded by automated scoring.

### Blocked Company Rejects

Counts company-exclusion-tagged `Jobs` rows and dated rejected rows with blocked-company evidence.

### Too-Senior Rejects or Penalties

Counts jobs added during the week that are Senior Director, VP, SVP, EVP, C-suite, or carry Sprint 42 too-senior audit tags.

### Review Completion Rate

`Jobs Reviewed / Jobs Added`

### Actionable Conversion Rate

`Reviewed jobs with an actionable current decision / Jobs Reviewed`

Actionable decisions include Interested, Watch, Deferred, Applied, Interviewing, Offer, Reviewing, and similar active states.

### Dismissal Rate

`Jobs Dismissed / Jobs Reviewed`

### Backlog Change

`Current week backlog / Prior week backlog`, expressed as the numeric difference rather than a rate.

### Signal Quality

`Strong Fit Jobs plus Stretch Fit Jobs / Jobs Added`

### Noise Removed

`Auto-Rejected Jobs / Total jobs considered`, where total considered includes accepted `Jobs` rows and dated `Rejected_Jobs` rows for the week.

## Historical limitations

The workbook does not currently store a complete append-only status event ledger. Historical transition metrics can therefore only be reconstructed from durable dates such as:

1. `first_seen_date`
2. `reviewed_date`
3. `application_date`
4. `last_application_update`
5. `closed_date`

The Notes column makes this limitation explicit. Future weekly rows become more reliable because completed rows are retained after they are written.

Sprint 44 does not add brittle manual date requirements to `Jobs`. A full status event ledger can be considered separately if later reporting requires exact historical state reconstruction.

## Worksheet behavior

`Weekly_Value` is a generated read-only surface.

1. Header row is frozen.
2. Week Start and Week End columns are frozen.
3. A basic filter covers the populated range.
4. Rate fields use percentage formatting.
5. System-managed headers use gray fill.
6. No Merge & Center behavior is used.
7. Existing user-entered review notes and decisions are not modified.

## Refresh commands

Normal refresh:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
.\.venv\Scripts\Activate.ps1
python -m src.weekly_value --refresh
```

Deterministic validation:

```powershell
python -m src.weekly_value --refresh --as-of 2026-07-09 --backfill-weeks 12
```

Focused tests:

```powershell
pytest tests/test_weekly_value.py tests/test_follow_up.py
```

## Automation

The `Job Tracker Weekly Value Refresh` GitHub Actions workflow runs after the normal daily ingestion window and can also be run manually.

The workflow:

1. Validates Google Sheets secrets.
2. Runs the focused Sprint 44 and Sprint 43 tests.
3. Refreshes `Weekly_Value`.
4. Writes current week metrics to the GitHub Actions summary.

The workflow does not depend on local PowerShell access.

## Validation checklist

Before merging:

1. Run the full pull request test suite.
2. Confirm the focused weekly value and follow-up tests pass.
3. Confirm `Jobs` schema order is unchanged.
4. Run the Weekly Value workflow manually on the branch if branch workflow permissions allow it.
5. Confirm `Weekly_Value` appears with one row per week.
6. Confirm the current week is first and is calculated through the refresh date.
7. Confirm the prior week remains filterable.
8. Confirm gray headers, filters, frozen columns, and percentage formats are applied.
9. Confirm blocked companies are not counted as viable strong fits.
10. Confirm Director roles count as stretch rather than strong fit.
11. Confirm Senior Director and VP roles count in too-senior suppression.
12. Confirm no manual review or application data changes in `Jobs`.
