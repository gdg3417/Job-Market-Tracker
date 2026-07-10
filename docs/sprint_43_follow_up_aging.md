# Sprint 43: Outstanding Status Aging and Follow-Up Reminders

## Purpose

Sprint 43 adds deterministic follow-up aging for active job-search statuses and creates a generated `Follow_Up_Queue` worksheet on top of the canonical `Jobs` table.

The sprint does not reorder, delete, or add canonical `Jobs` columns. It reuses existing manual fields:

- `review_status`
- `reviewed_date`
- `application_status`
- `application_date`
- `interview_stage`
- `last_application_update`
- `next_action`
- `next_action_date`
- `follow_up_date`
- `review_notes`

## Status aging rules

| Normalized status | Calendar-day threshold |
| --- | ---: |
| Applied | 7 |
| In Review | 7 |
| Recruiter Screen | 4 |
| Hiring Manager Screen | 4 |
| Interviewing | 4 |
| Take-home / Case | 4 |
| Waiting on Response | 6 |
| Offer / Negotiation | 2 |

Calendar days are used initially. Business-day calculations can be added later if needed.

## Status detection

The current outstanding status is derived from the existing application and review fields.

1. Terminal statuses such as Dismissed, Rejected, Closed, Withdrawn, Not Reviewed, Not Started, and Drafting do not produce follow-up reminders.
2. `interview_stage` is used to distinguish recruiter screens, hiring manager screens, interviews, cases, waiting periods, and offer negotiation.
3. Applied and In Review are derived from `application_status` and `review_status`.
4. Waiting language in `next_action` can route an otherwise active row to Waiting on Response.

## Last status update date

The calculation uses the most recent applicable date from:

1. `last_application_update`
2. `application_date`
3. `reviewed_date`

The generated logic intentionally does not use `updated_at`. That field can change during ingestion, scoring, or enrichment and would incorrectly reset follow-up aging.

For best results, update `last_application_update` whenever an application-stage status changes. Existing `application_date` and `reviewed_date` provide safe fallback dates.

If an active status has no usable status date, the row is flagged as follow-up due with a clear missing-date reason. This avoids silently losing active applications from the follow-up queue.

## Explicit follow-up dates

`next_action_date` and `follow_up_date` override the normal aging threshold when either date is due.

The generated queue does not overwrite either field and does not overwrite user notes.

## Follow_Up_Queue design

The generated worksheet includes:

- Job identity and URL
- Normalized outstanding status
- Last status update date
- Days since status update
- Follow-up due flag
- Human-readable follow-up reason
- Existing next action and follow-up dates
- Existing application, review, and interview fields
- Existing review notes

The worksheet is filterable, freezes the header row and identity columns, and sorts due rows first.

`Follow_Up_Queue` is read-only. Manual edits continue to belong in `Jobs`.

## Refresh command

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
.\.venv\Scripts\Activate.ps1
python -m src.follow_up --refresh
```

For deterministic validation:

```powershell
python -m src.follow_up --refresh --as-of 2026-07-09
```

## Manual workflow

1. Refresh `Follow_Up_Queue`.
2. Filter `follow_up_due` to TRUE.
3. Review the reason, current status, next action, and existing notes.
4. Update the canonical row in `Jobs`.
5. Set `last_application_update` to the current date when changing an application-stage status.
6. Update `next_action` and `next_action_date` when a specific follow-up is planned.
7. Refresh `Follow_Up_Queue` again.

## Non-destructive behavior

Sprint 43 does not:

- Change a role to Rejected, Dismissed, or Closed
- Modify `review_notes`
- Modify application or review statuses
- Modify manual dates
- Change canonical Jobs schema order
- Treat Not Reviewed Yet as an application follow-up item

Review backlog reporting remains separate from follow-up reporting.
