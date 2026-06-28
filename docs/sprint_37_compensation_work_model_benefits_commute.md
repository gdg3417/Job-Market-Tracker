# Sprint 37: Compensation, Work Model, Benefits, and Commute Intelligence

## Purpose

Sprint 37 adds structured decision evidence for compensation, work model, benefits, office location, and commute so roles can be compared against the actual move criteria in the target profile.

Automated scoring, potential priority, verified fit, review state, application state, and lifecycle state remain separate concepts. Move value is an additional decision layer and does not rewrite the existing score fields.

## User configuration

Move value criteria are loaded from the target profile where available.

The current target profile already stores these decision thresholds and preferences:

1. Current base compensation.
2. Current target bonus percent.
3. Current commute distance and commute time.
4. Senior Manager base floor.
5. Director preferred base floor.
6. Serious move total compensation threshold.
7. Target total compensation range.
8. Work-model preferences.
9. Commute scoring buckets.

If a setting is missing, the Python model uses conservative defaults that match the current profile assumptions.

## Jobs schema fields

Sprint 37 appends structured fields to the canonical Jobs schema. The fields are trailing columns so workbook migration remains backward compatible.

### Compensation fields

1. `base_salary_min`
2. `base_salary_max`
3. `salary_currency`
4. `bonus_target_percent`
5. `bonus_max_percent`
6. `commission_estimate`
7. `equity_or_lti_estimate`
8. `sign_on_bonus`
9. `estimated_total_comp_min`
10. `estimated_total_comp_max`
11. `compensation_source_type`
12. `compensation_source_url`
13. `compensation_observed_date`
14. `compensation_confidence`
15. `compensation_notes`

### Work-model fields

1. `required_office_days_per_week`
2. `travel_percentage`
3. `relocation_required`
4. `geographic_eligibility`
5. `work_model_source`
6. `work_model_confidence`
7. `work_model_notes`

The existing `remote_status` and `work_model` fields remain in place. Sprint 37 normalizes `work_model` into `remote`, `hybrid`, `on_site`, or `unknown`.

### Office and commute fields

1. `office_name`
2. `office_street_address`
3. `office_city`
4. `office_state`
5. `office_postal_code`
6. `location_confidence`
7. `estimated_one_way_distance`
8. `estimated_one_way_travel_time`
9. `commute_bucket`
10. `commute_calculation_date`
11. `commute_method`
12. `commute_notes`

### Benefits fields

1. `benefit_401k_match`
2. `health_insurance_indicators`
3. `paid_parental_leave`
4. `pto`
5. `pension`
6. `tuition_reimbursement`
7. `other_material_benefits`
8. `benefits_source`
9. `benefits_confidence`
10. `benefits_notes`

### Move-value fields

1. `compensation_improvement`
2. `total_compensation_improvement`
3. `work_model_improvement`
4. `commute_improvement`
5. `benefits_confidence_summary`
6. `scope_p_and_l_modifier`
7. `move_value_classification`
8. `move_value_notes`
9. `move_value_updated_at`
10. `decision_evidence_conflict_notes`

## Evidence-source hierarchy

Compensation source types are controlled values:

1. `user_entered`
2. `recruiter_provided`
3. `employer_posted`
4. `application_form`
5. `government_disclosure`
6. `trusted_external_estimate`
7. `inferred_from_title`
8. `unknown`

The merge logic preserves stronger evidence over weaker evidence. User-entered evidence ranks highest because it captures explicit review decisions, recruiter calls, application forms, or manually validated information.

## Confirmed versus estimated compensation

Confirmed compensation means the job has a compensation amount from one of these source types:

1. `employer_posted`
2. `recruiter_provided`
3. `application_form`
4. `government_disclosure`
5. `user_entered`

Estimated compensation means the amount came from `trusted_external_estimate` or `inferred_from_title`.

Unknown compensation means no usable amount is present.

Estimated compensation must not be presented as confirmed. Missing compensation does not make a job low quality by itself.

## Compensation calculation

Base salary is stored separately from total compensation. Total compensation estimate can include:

1. Base salary.
2. Bonus target percent.
3. Bonus maximum percent.
4. Commission estimate.
5. Equity or long-term incentive estimate.
6. Sign-on bonus.

The total compensation calculation is transparent and reproducible from the workbook fields.

## Work-model methodology

Work model is normalized into:

1. `remote`
2. `hybrid`
3. `on_site`
4. `unknown`

Required office days per week are stored separately so a two-day hybrid role and a four-day hybrid role do not receive the same interpretation.

## Commute methodology

Sprint 37 does not use paid routing APIs.

The commute model supports zero-added-cost evidence from:

1. User-entered distance or time.
2. Explicit office address or city.
3. Conservative distance or time estimates.
4. User-defined commute buckets.

Commute buckets are:

1. `under_15_minutes`
2. `15_to_30_minutes`
3. `30_to_45_minutes`
4. `over_45_minutes`
5. `unknown`

If live routing is unavailable, commute evidence must be labeled with method, confidence, and notes.

## Benefits evidence rules

Benefits evidence is structured but conservative.

Do not infer detailed benefits from generic employer branding. Store generic indicators in notes unless the posting, recruiter, application form, or user-entered evidence provides a usable detail.

## Move-value formula

Move value compares the job against the current role using these components:

1. Compensation improvement.
2. Total compensation improvement.
3. Work-model improvement.
4. Commute improvement.
5. Benefits confidence.
6. Scope or P&L-path modifier.

Classifications are:

1. `clearly_better`
2. `potentially_better`
3. `lateral_or_uncertain`
4. `worse`
5. `insufficient_evidence`

Missing evidence is classified separately from negative evidence. A role with strong title, scope, or P&L path signals but missing compensation remains visible for follow-up.

## Dashboard sections

Sprint 37 adds these Dashboard sections:

1. Move-value intelligence summary.
2. Strong roles with confirmed compensation.
3. Strong roles with unknown compensation.
4. Remote or hybrid opportunities.
5. Short-commute opportunities.
6. Five-day on-site penalties.
7. Roles meeting serious-move compensation.
8. Roles requiring compensation follow-up.
9. Roles requiring work-model follow-up.

Existing Dashboard and Digest sections remain active.

## User-entered evidence

User-entered compensation and work-model evidence is durable. It should not be overwritten by automated enrichment or weaker estimates.

Conflicting evidence is preserved through `decision_evidence_conflict_notes` rather than silently replacing strong evidence.

## Production validation

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git switch main
git pull --ff-only origin main
git fetch origin

git switch codex/sprint-37-comp-workmodel-commute
git pull --ff-only origin codex/sprint-37-comp-workmodel-commute

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation
```

Run bounded enrichment and refresh presentation:

```powershell
python -m src.enrichment.production --run --mode daily --resolution-limit 5 --company-limit 5 --direct-limit 5 --external-limit 0 --lifecycle-limit 0
python -m src.dashboard --no-run-log
python -m src.schema --validate
```

Workbook checks:

1. Confirm the Jobs tab has the Sprint 37 trailing fields.
2. Inspect Topgolf and Toyota evidence.
3. Add one user-entered compensation observation to an existing strong role.
4. Set `compensation_source_type` to `user_entered` and `compensation_confidence` to `confirmed`.
5. Add one user-entered work-model observation to an existing role.
6. Set `work_model_source` to `user_entered` and `work_model_confidence` to `confirmed`.
7. Rerun the bounded enrichment and dashboard refresh commands above.
8. Confirm user-entered compensation evidence remains intact.
9. Confirm user-entered work-model evidence remains intact.
10. Review move-value classifications on the Dashboard.
11. Confirm estimated compensation is not labeled confirmed.
12. Confirm missing compensation does not suppress strong potential roles.
13. Confirm no duplicate Jobs, Runs, Dashboard, or Digest rows were created.

## Rollback considerations

1. Revert the Sprint 37 PR to remove decision-evidence code, tests, documentation, and Dashboard additions.
2. If the workbook was migrated, leave appended Jobs columns in place unless explicit cleanup is approved. Blank trailing columns are low risk and preserve manual evidence if any was entered.
3. Do not delete review, application, enrichment, resolver, lifecycle, or source health data. Sprint 37 does not own those records.

## Intentionally deferred

1. Paid routing APIs or live commute routing.
2. Automatic scoring-weight changes from compensation or move-value evidence.
3. Inferring detailed benefits from generic employer branding.
4. Lifecycle maturity, regression evaluation, production readiness gates, and alerts from Sprint 38.
5. Broad regression-dataset expansion from Sprint 38.
