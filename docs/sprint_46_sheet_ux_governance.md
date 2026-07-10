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

The governance layer uses existing model values instead of creating a second status vocabulary.

Dropdowns are applied to these `Jobs` fields when present:

1. `review_status`
2. `interest_decision`
3. `dismissal_reason`
4. `application_status`
5. `work_model`
6. `compensation_source_type`
7. `compensation_confidence`
8. `work_model_confidence`
9. `location_confidence`
10. `benefits_confidence`
11. `required_office_days_per_week`

Boolean dropdowns are also applied to existing controlled fields on the configuration tabs.

Fields such as `interview_stage`, `next_action`, `manual_priority`, and `manual_fit_rating` remain editable without a dropdown because the current model does not define a closed set of valid values for them. Sprint 46 does not invent a conflicting vocabulary.

## Manual fields in Jobs

Green `Jobs` columns cover the existing review and application workflow plus user-owned decision evidence, including:

1. Review status, decision, notes, priority, rating, reviewed date, and authoritative URL.
2. Dismissal reason and detail.
3. Application status, dates, documents, referral or contact, interview stage, and next action.
4. User-entered compensation, work-model, office, commute, benefits, and evidence-source details.

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
3. Applies header colors, dropdowns, filters, freezes, and the `Sheet_Guide` tab.
4. Writes a GitHub Actions summary with governed-sheet and dropdown counts.

## Post-merge validation

Run `Job Tracker Sheet UX Governance` manually on `main`, then verify:

1. `Jobs` has green headers only on safe manual columns.
2. `Jobs.review_status`, `Jobs.application_status`, and the other controlled fields show dropdowns.
3. System-managed `Jobs` headers are gray.
4. `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, and `Digest` remain gray and generated.
5. Configuration-tab headers are green.
6. Filters and frozen headers work on tabular sheets.
7. Existing review notes, statuses, dates, and evidence values remain unchanged.
8. `Sheet_Guide` exists and explains where edits belong.

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
python -m src.sheet_governance --apply
```
