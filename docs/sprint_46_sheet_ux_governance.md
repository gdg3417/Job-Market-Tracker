# Sprint 46: Sheet UX Governance, Header Colors, and Dropdowns

## Objective

Make the Job Market Tracker workbook safer and faster to use without changing the canonical `Jobs` schema or making generated review surfaces writable.

## Workbook behavior

### Header colors

1. Green headers identify fields that are safe for manual edits.
2. Gray headers identify imported, formula-driven, derived, generated, or otherwise system-managed fields.
3. Any new or unknown `Jobs` field defaults to gray until it is deliberately classified as user-editable.
4. `Review_Queue`, `Follow_Up_Queue`, `Dashboard`, `Digest`, `Weekly_Value`, and `Weekly_Context` remain generated read-only surfaces. Make review, application, follow-up, and evidence changes in green `Jobs` columns.
5. `Config_Searches`, `Config_Companies`, `Scoring_Rules`, and `Target_Companies` are user-managed configuration tabs, so their populated headers are green.

### Controlled dropdowns

The governance layer uses existing model values and established workflow scales instead of creating a second status vocabulary.

Dropdowns are applied to these `Jobs` fields when present:

1. `review_status`
2. `interest_decision`
3. `manual_priority`, using the existing 1 through 5 action-priority scale
4. `manual_fit_rating`, using the existing 1 through 10 calibration scale
5. `dismissal_reason`
6. `application_status`
7. `work_model`
8. `work_model_source`
9. `compensation_source_type`
10. `compensation_confidence`
11. `work_model_confidence`
12. `location_confidence`
13. `benefits_confidence`
14. `required_office_days_per_week`

Optional controlled fields include a blank dropdown choice so a prior decision, rating, source, or office-day value can be cleared without entering invalid data.

Boolean dropdowns are also applied to existing controlled fields on the configuration tabs.

Fields such as `interview_stage` and `next_action` remain editable without a dropdown because the current model does not define a closed set of valid values for them. Sprint 46 does not invent a conflicting stage or action vocabulary.

## Manual fields in Jobs

Green `Jobs` columns cover the existing review and application workflow plus user-owned decision evidence, including:

1. Review status, decision, notes, priority, rating, reviewed date, and authoritative URL.
2. Dismissal reason and detail.
3. Application status, dates, documents, referral or contact, interview stage, and next action.
4. User-entered compensation, work-model, office, commute, benefits, and evidence-source details.

When manually overriding compensation or work-model evidence, set the related source field to `user_entered`. This ensures later evidence merges treat the manual value as authoritative rather than replacing it with a higher-ranked automated source.

Derived outputs remain gray, including scores, potential priority, enrichment and lifecycle state, estimated total compensation, commute bucket, move-value classification, and conflict outputs.

## Sheet behavior

1. `Jobs` remains the canonical source of truth and its column order is not changed.
2. Existing values are not rewritten by governance.
3. Generated surfaces remain filterable and keep useful frozen rows and identity columns.
4. The governance process uses formatting and data-validation requests only on existing project tabs.
5. A generated `Sheet_Guide` tab explains the color system and identifies the edit mode for each governed worksheet.
6. No merged-cell operation is used.
7. Missing optional generated tabs are reported as warnings instead of failing the run.

## Automation

The `Job Tracker Sheet UX Governance` workflow can be run manually and is also scheduled after the existing workbook refresh windows. It:

1. Runs focused Sprint 46 tests.
2. Validates governance definitions without connecting to Sheets.
3. Validates the live canonical workbook schema before applying formatting or validation rules.
4. Applies header colors, dropdowns, filters, freezes, and the `Sheet_Guide` tab.
5. Writes a GitHub Actions summary with governed-sheet and dropdown counts.

## Post-merge validation

Run `Job Tracker Sheet UX Governance` manually on `main`, then verify:

1. `Jobs` has green headers only on safe manual columns.
2. `Jobs.review_status`, `Jobs.application_status`, manual priority, manual fit rating, evidence-source fields, and the other controlled fields show dropdowns.
3. Optional controlled fields can be returned to blank.
4. System-managed `Jobs` headers are gray.
5. `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, and `Digest` remain gray and generated.
6. Configuration-tab headers are green.
7. Filters and frozen headers work on tabular sheets.
8. Existing review notes, statuses, dates, and evidence values remain unchanged.
9. `Sheet_Guide` exists and explains where edits belong.

No local PowerShell validation is required before merge when GitHub Actions passes.

Optional local validation:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git fetch origin
git switch codex/sprint-46-sheet-ux-governance
git pull --ff-only origin codex/sprint-46-sheet-ux-governance
.\.venv\Scripts\Activate.ps1
pytest tests/test_sheet_governance.py
python -m src.sheet_governance --validate
python -m src.schema --validate
python -m src.sheet_governance --apply
```