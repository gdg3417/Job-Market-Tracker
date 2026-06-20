# Sprint 20: Weekly Email Digest and Dashboard Usability

## Goal

Sprint 20 makes the tracker easier to use during weekly review.

After this sprint, the Dashboard should answer whether there is anything worth reviewing or acting on. The Sheet can also send a weekly digest email from Google Apps Script.

## Changes

1. `src.dashboard` writes plain Dashboard values instead of spreadsheet formulas.
2. The Dashboard now has an executive answer, action queue, tracker health, source health, top roles, and source cleanup queue.
3. Digest sections are capped for weekly review.
4. `apps_script/weekly_digest_email.gs` provides a bound Apps Script email sender.
5. Email remains outside GitHub Actions.

## Dashboard sections

1. Executive answer
2. Action queue
3. Tracker health
4. Source health
5. Top roles to review
6. Source cleanup queue

The executive answer can be:

1. `Review roles now`
2. `Review strong fits this week`
3. `Review target company roles`
4. `Source cleanup needed`
5. `No action needed this week`

## Decision logic

1. Immediate review rows trigger `Review roles now`.
2. Strong fit rows trigger `Review strong fits this week`.
3. Target company watchlist rows trigger `Review target company roles`.
4. High rejected source audit rows with no actionable roles trigger `Source cleanup needed`.
5. Otherwise the answer is `No action needed this week`.

## Digest caps

| Section | Cap |
| --- | ---: |
| Immediate review | 10 |
| Strong fit | 10 |
| Target company watchlist | 10 |
| Needs salary research | 10 |
| Remote or short commute | 10 |
| P&L pathway | 10 |
| New this week | 10 |
| Closed or likely closed this week | 10 |
| Rejected source audit | 5 |

## Apps Script setup

Use this after the branch is merged and `python -m src.dashboard` refreshes the Sheet.

1. Open the Job Market Tracker Google Sheet.
2. Select Extensions, then Apps Script.
3. Open `apps_script/weekly_digest_email.gs` in the repo.
4. Copy the full script into the Apps Script editor.
5. Save the project.
6. Select `sendTestWeeklyDigest` from the function dropdown.
7. Click Run and authorize the script.
8. Reload the Sheet.
9. Open the `Job Tracker` menu.
10. Select `Send test weekly digest`.
11. Confirm the test email is received.

The Sheet menu added by the script is:

```text
Job Tracker
  Send test weekly digest
  Send weekly digest now
```

## Weekly trigger setup

To create the Monday morning trigger:

1. Open Apps Script.
2. Select `createMondayMorningWeeklyDigestTrigger`.
3. Click Run.
4. Confirm a trigger exists for `sendWeeklyDigestNow`.

The trigger is configured for Monday around 8:00 AM Central.

To disable it:

1. Open Apps Script.
2. Select `deleteWeeklyDigestTriggers`.
3. Click Run.

## Recipient setup

The script tries to send to the active or effective Google user. If that is blank in your Google account setup, set the document property `JOB_TRACKER_DIGEST_RECIPIENT` to the recipient email address.

## Email behavior

The weekly email reads the `Digest` tab and includes:

1. Immediate review
2. Strong fit
3. Target company watchlist
4. Needs salary research
5. Remote or short commute
6. P&L pathway
7. New this week
8. Rejected source audit summary

If actionable roles exist, the subject is:

```text
Job Tracker Weekly Digest: X roles to review
```

If no actionable roles exist, the subject is:

```text
Job Tracker Weekly Digest: No strong fits this week
```

The email includes links to the Sheet, Dashboard tab, Digest tab, and job postings with `canonical_url`.

## Validation

Run from PowerShell in the repo root:

```powershell
pytest
python -m src.schema --validate
python -m src.dashboard
```

Then test the email from the Sheet:

```text
Job Tracker > Send test weekly digest
```

Expected results:

1. Tests pass.
2. Schema validation passes.
3. Dashboard refresh succeeds.
4. Dashboard has no `#REF!` or `#VALUE!`.
5. Dashboard shows a clear answer at the top.
6. Rejected source audit rows are separate from real job review rows.
7. Test weekly digest email sends.
8. The Monday trigger can be created and disabled.

## Out of scope

1. Sending email from GitHub Actions.
2. Adding email send scope to Python OAuth.
3. Slack or SMS alerts.
4. Major scoring changes.
5. Major source expansion.
6. Resume tailoring.
7. Automatic applications.
