# Sprint 36: Human Review, Application Workflow, and Learning Loop

## Purpose

Sprint 36 adds structured human review and application state so the tracker can learn from actual decisions without changing automated scoring weights.

Automated potential priority, verification state, evidence completeness, verified fit, manual priority, review status, application status, and lifecycle status remain separate concepts.

## Review fields

The Jobs schema now stores these trailing review and application fields:

- `review_status`
- `reviewed_date`
- `reviewer`
- `interest_decision`
- `manual_priority`
- `manual_fit_rating`
- `manual_authoritative_url`
- `review_notes`
- `follow_up_date`
- `dismissal_reason`
- `dismissal_detail`
- `application_status`
- `application_date`
- `application_url`
- `resume_version`
- `cover_letter_version`
- `referral_or_contact`
- `interview_stage`
- `last_application_update`
- `next_action`
- `next_action_date`
- `manual_decision_conflict`

The fields are appended to the canonical Jobs schema so workbook migration remains backward compatible.

## Review statuses

Supported review statuses are:

- `not_reviewed`
- `review_now`
- `reviewing`
- `interested`
- `watch`
- `deferred`
- `dismissed`
- `applied`
- `interviewing`
- `offer`
- `rejected`
- `withdrawn`
- `closed`

Invalid values normalize to `not_reviewed` on model load. Review status is independent from posting lifecycle status.

## Status transitions

The review workflow validates transitions before applying explicit review updates.

Typical progression:

```text
not_reviewed
  -> review_now or reviewing
  -> interested, watch, deferred, dismissed, applied, or closed
  -> interviewing
  -> offer, rejected, withdrawn, or closed
```

Regression from applied workflow back to `not_reviewed` is invalid. Rejected, withdrawn, and closed records can be reopened to `review_now` for explicit reconsideration.

## Dismissal reasons

Supported dismissal reasons are:

- `compensation_too_low`
- `commute_too_long`
- `on_site_requirement`
- `wrong_seniority`
- `role_too_junior`
- `role_too_senior`
- `too_much_fp_and_a`
- `weak_p_and_l_path`
- `weak_operating_scope`
- `industry_excluded`
- `company_not_attractive`
- `benefits_not_compelling`
- `role_closed`
- `duplicate`
- `recruiting_intermediary`
- `insufficient_improvement`
- `not_interested`
- `other`

Free-text context belongs in `dismissal_detail`, not in the controlled reason field.

## Manual priority behavior

Manual priority is separate from automated scoring.

Rules:

- Manual priority never rewrites `total_score`, `potential_priority_score`, `verified_total_score`, or `verified_alert_tier`.
- Higher manual priority moves a role upward in action-queue sorting.
- Blank manual priority removes the manual override.
- Manual priority survives enrichment reruns and duplicate merges.
- Manual fit rating is preserved for calibration reporting and does not alter production scoring weights.

## Duplicate merge behavior

When duplicate jobs are compared, the review workflow preserves the most advanced manual decision state.

Rules:

- Do not regress from `applied`, `interviewing`, or `offer` to `not_reviewed`.
- Preserve application URLs, resume version, cover letter version, referral or contact, notes, next action, and due dates where present.
- Preserve dismissal decisions and reasons.
- Keep the earliest application date and latest application update date.
- Merge non-duplicate notes without overwriting existing notes.
- Flag conflicting manual decisions in `manual_decision_conflict` for human review.

## Application workflow

Application state is tracked through Jobs fields and can be surfaced through the review dashboard helper.

Main queues:

- Review now
- Interested
- Deferred follow-ups
- Applications submitted
- Interviews in progress
- Offers
- Stale applications needing follow-up
- Upcoming next actions

Application fields are durable workbook data. Critical workflow logic remains in Python rather than Apps Script.

## Feedback and calibration report

Sprint 36 produces calibration metrics only. It does not change production scoring weights.

Metrics include:

- Review rate
- Interest rate
- Application rate
- Dismissal reasons
- Roles reviewed by score band
- Roles reviewed by role family
- Roles reviewed by company tier
- Automated score versus manual fit
- False positives
- Potential missed opportunities

Interpretation:

- High false positives mean high-scoring roles are being dismissed and should be reviewed for future scoring calibration.
- Potential missed opportunities mean low-scoring roles received positive manual signals and may reveal underweighted title, scope, company, or P&L-path signals.
- A positive manual-fit delta means human ratings tend to exceed the automated score band.

Any scoring adjustment must be made in a later authorized sprint or explicit calibration task.

## Production validation

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
git fetch origin

git switch codex/sprint-36-human-review-application-workflow
git pull --ff-only origin codex/sprint-36-human-review-application-workflow

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation
python -m src.dashboard --no-run-log
```

Workbook validation:

1. Confirm the Jobs tab has the new trailing review and application fields.
2. Refresh Dashboard and Digest.
3. Manually review at least five existing roles.
4. Mark one role `interested`.
5. Mark one role `deferred` and set `follow_up_date`.
6. Mark one role `dismissed` and set a controlled `dismissal_reason`.
7. Mark one role `applied` and set `application_date`, `application_url`, and `resume_version` if applicable.
8. Mark one role `review_now` with `manual_priority`.
9. Run a bounded production refresh:

```powershell
python -m src.enrichment.production --run --mode daily --resolution-limit 5 --company-limit 5 --direct-limit 5 --external-limit 0 --lifecycle-limit 0
python -m src.schema --validate
python -m src.dashboard --no-run-log
```

Confirm:

1. Manual decisions remain intact after the rerun.
2. Automated score fields are unchanged by manual priority.
3. Duplicate merging does not regress applied, dismissed, or interested records.
4. Conflicting manual decisions are flagged for review.
5. Review and application queues can be generated from workbook data.
6. Calibration metrics report review, interest, application, dismissal, false-positive, and potential-missed-opportunity signals.

## Intentionally deferred

- Automatic scoring-weight changes from review decisions.
- Compensation, work model, benefits, and commute intelligence from Sprint 37.
- Lifecycle maturity, regression evaluation, readiness gates, and alerts from Sprint 38.
- Moving workflow logic into Apps Script.
