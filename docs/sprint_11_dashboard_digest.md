# Sprint 11: Dashboard and Digest

## Purpose

Sprint 11 makes the Google Sheet easier to review each week. It refreshes two tabs:

1. `Dashboard`, formula-driven summary metrics and rollups.
2. `Digest`, a generated review queue grouped by action-oriented sections.

The workflow is still zero added cost. It uses Python, Google Sheets, and an optional Google Apps Script email helper.

## Run command

From the repo root:

```powershell
python -m src.dashboard
```

To refresh `Dashboard` and `Digest` without writing a `Runs` row:

```powershell
python -m src.dashboard --no-run-log
```

## Dashboard sections

The `Dashboard` tab includes:

1. New jobs this week
2. Immediate review jobs
3. Strong fit open jobs
4. Track-only open jobs
5. P&L pathway jobs
6. Remote jobs
7. Jobs within 15 minutes
8. Jobs within 30 minutes
9. Closed jobs this week
10. Jobs with missing salary
11. Jobs by role family
12. Jobs by company
13. Jobs by source
14. Jobs by alert tier
15. Salary range by role family
16. Average days open by role family
17. Companies with repeat postings

## Digest sections

The `Digest` tab is generated from the current `Jobs` rows and groups postings into:

1. Immediate review
2. Strong fit
3. P&L pathway
4. Remote, hybrid, or short commute
5. New this week
6. Closed or likely closed this week
7. Missing salary review

Rows may appear in more than one section. This is intentional because the Digest is a review queue, not a normalized fact table.

## Run logging

A successful refresh appends one row to `Runs` with `run_type` equal to:

```text
sprint_11_dashboard_digest
```

The run row includes counts for jobs read, digest rows written, immediate review rows, strong fit rows, P&L pathway rows, and remote or short-commute rows.

## Optional weekly email

`scripts/weekly_digest_email.gs` can be installed in the bound Apps Script project for the Google Sheet. Update `RECIPIENT_EMAIL`, then create a weekly time-driven trigger for `emailWeeklyJobTrackerDigest`.

This is optional. The core Sprint 11 workflow does not require Apps Script.
