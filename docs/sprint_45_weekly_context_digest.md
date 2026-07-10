# Sprint 45: Weekly Context Digest

## Objective

Sprint 45 adds a concise weekly email contract that uses the existing `Weekly_Value` metrics instead of rebuilding dashboard calculations inside email code.

The full metric set remains in `Weekly_Value`. The email focuses on action items, new strong and stretch matches, a small set of weekly metrics, follow-ups, and noise removed.

`Jobs` remains the canonical source-of-truth table. Sprint 45 does not add, remove, reorder, or rewrite canonical `Jobs` columns.

## Generated worksheet

The workflow creates a generated, read-only `Weekly_Context` tab.

The tab contains these sections:

1. Action Needed
2. New Strong Matches
3. Weekly Tracker Metrics
4. Backlog and Follow-up
5. Noise Removed

The contract includes direct `Jobs` row numbers and posting URLs where available. The Apps Script sender converts these into links to the relevant tracker row and job posting.

## Default email content

The default configuration includes:

1. Jobs Added
2. Jobs Reviewed
3. Jobs Still Not Reviewed Yet
4. Jobs Applied
5. Follow-ups Due
6. Strong Fit Jobs
7. Stretch Fit Jobs
8. Auto-Rejected Jobs
9. Blocked Company Rejects
10. Up to five new strong or stretch matches
11. Up to five additional roles needing review
12. Up to five follow-up items

The following dashboard metrics remain excluded from email by default:

1. Jobs Dismissed
2. Jobs Moved to Active Status
3. Outstanding Active Roles
4. Too-Senior Rejects or Penalties
5. Review Completion Rate
6. Actionable Conversion Rate
7. Dismissal Rate
8. Backlog Change
9. Signal Quality
10. Noise Removed rate

These metrics remain available in `Weekly_Value` and can be enabled through configuration.

## Shared logic

Sprint 45 does not duplicate the weekly dashboard calculations.

1. Weekly totals come directly from the selected `Weekly_Value` row.
2. Strong and stretch role classification reuses Sprint 44 fit logic.
3. Review ordering reuses `Review_Queue` eligibility and sort behavior.
4. Follow-up selection reuses Sprint 43 aging logic.
5. Google Sheets date normalization reuses the Sprint 44 production normalization path.

## Configuration

Email scope is controlled by:

```text
config/weekly_digest.yml
```

Default configuration:

```yaml
weekly_digest:
  summary_week: latest_completed
  top_review_limit: 5
  top_follow_up_limit: 5
  top_new_match_limit: 5
  include_dashboard_only_metrics: false
  include_optional_metrics: []
```

Supported summary modes:

1. `latest_completed`, recommended for Monday email delivery
2. `current`, for the current Monday through Sunday row
3. `latest_available`, for the newest available dashboard row

Optional metrics must match a `Weekly_Value` header. Invalid metric names are ignored safely.

## Workflow behavior

The existing `Job Tracker Weekly Value Refresh` workflow now refreshes both:

1. `Weekly_Value`
2. `Weekly_Context`

The workflow retains its normal daily schedule and adds a Monday pre-email refresh at 12:00 UTC. This normally runs at 07:00 AM Central during daylight time and 06:00 AM Central during standard time, before the Apps Script email trigger near 08:00 AM Central.

The workflow summary reports:

1. Weekly Value status and headline metrics
2. Weekly Context status
3. Summary week
4. Review item count
5. Follow-up item count
6. New match count

## Apps Script setup

Sprint 45 preserves the existing sender in:

```text
apps_script/weekly_digest_email.gs
```

Add the new file to the same bound Apps Script project:

```text
apps_script/weekly_context_digest.gs
```

The new script uses shared helper functions from the existing sender. Both files must be present in the same Apps Script project.

### Test the new email

From the Apps Script editor, run:

```text
sendTestWeeklyContextDigest
```

The function sends the new context email when `Weekly_Context` is available. If the context tab is missing or empty, it falls back to the existing weekly digest.

### Activate the Monday trigger

After validating the test email, run:

```text
createMondayMorningWeeklyContextDigestTrigger
```

This removes existing triggers for both the legacy and new weekly sender, then creates one Monday trigger for:

```text
sendWeeklyContextDigestNow
```

The trigger is configured near 08:00 AM Central.

### Remove weekly sender triggers

Run:

```text
deleteWeeklyContextDigestTriggers
```

## Manual refresh without PowerShell

After merge:

1. Open the repository in GitHub.
2. Select Actions.
3. Select `Job Tracker Weekly Value Refresh`.
4. Select `Run workflow` on `main`.
5. Leave the default 12-week backfill.
6. Review the workflow Step Summary.
7. Confirm that `Weekly_Context` exists in the Google Sheet.
8. Confirm the summary week and action lists are reasonable.

## Optional local validation

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
.\.venv\Scripts\Activate.ps1
pytest tests/test_weekly_value*.py tests/test_follow_up.py tests/test_weekly_context*.py
python -m src.weekly_value_sheet_dates --refresh --backfill-weeks 12
python -m src.weekly_context --refresh
```

## Safety and formatting

1. No canonical `Jobs` schema changes.
2. No manual review, application, follow-up, or notes fields are overwritten.
3. `Weekly_Context` is generated and read-only.
4. System-managed headers use gray fill.
5. Body cells receive white fill.
6. The tab has a filter and frozen headers.
7. No Merge & Center behavior is used.
8. Blocked companies, automated rejects, too-senior roles, and terminal postings cannot appear as normal review recommendations.
9. Director stretch roles remain visible as stretch matches.
10. Manager and Senior Manager strong fits remain prioritized.

## Validation checklist

Before merge:

1. Confirm all Sprint 45 tests pass.
2. Confirm the full repository test suite passes.
3. Confirm the workflow YAML is valid.
4. Confirm `Weekly_Context` uses `Weekly_Value` values instead of recalculating weekly metrics.
5. Confirm dashboard-only metrics are excluded by default.
6. Confirm optional metrics can be enabled through configuration.
7. Confirm strong and stretch roles are both represented.
8. Confirm follow-up due items include reasons and row links.
9. Confirm empty weeks render cleanly.
10. Confirm missing optional fields do not break rendering.
11. Confirm terminal postings are excluded from new-match and review recommendations.
12. Confirm the existing weekly digest remains available as a fallback.
13. Confirm no canonical schema or manual data changes occur.
