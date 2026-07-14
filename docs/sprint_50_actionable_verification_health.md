# Sprint 50: Actionable Verification Health and Funnel Semantics

## Objective

Sprint 50 separates current operational verification work from historical portfolio coverage.

The overall health classification now prioritizes roles that can still affect a current decision. Historical and terminal records remain visible through portfolio coverage metrics, but they no longer create actionable service-level debt.

## Actionable-role policy

`src.verification_health_actionability` applies one reusable classification to canonical `Jobs` rows.

Actionable roles include:

* Open and reopened roles that are not excluded
* Interested, watch, and actively reviewed roles
* Active applications and interviews
* Deferred roles whose follow-up date is due
* Deferred roles with a missing or invalid due date, because they require manual correction
* Likely closed or not-seen-once roles until authoritative closure is confirmed
* Roles that still need an authoritative posting or manual resolution

The actionable set excludes:

* Confirmed closed, closed, and expired roles
* Rejected, withdrawn, and closed applications
* Manually dismissed and other terminal review decisions
* Blocked companies
* Hard scoring exclusions
* Too-senior hard exclusions
* Deferred roles whose follow-up date is still in the future
* Nonblank malformed rows without a job key, company, and title

Google Sheets dates are normalized through `src.sheet_dates` before deferred-date evaluation and downstream health calculations.

## Operational health and portfolio coverage

The health output contains two separate sections.

### Actionable verification health

This section drives the overall score and classification. It includes:

* Actionable open roles
* Actionable high-potential roles
* Actionable unverified roles
* Unique primary blocker rows
* Aged actionable roles
* Manual interventions
* Active applications
* Likely closed roles awaiting closure confirmation
* Dismissed and not-yet-due deferred roles excluded from actionable health

### Portfolio evidence coverage

This section is informational and does not inflate current verification debt. It includes:

* Total valid portfolio jobs
* Open or uncertain postings
* Terminal postings
* Verified jobs
* Jobs with an authoritative posting
* Jobs with accepted evidence
* Covered jobs and coverage rate
* Average evidence completeness
* Invalid identity rows

## Blocker semantics

Each actionable role receives no more than one primary blocker.

Primary blockers are divided into:

* System work
* Manual intervention

Supporting gaps are counted separately. A role may therefore have one primary blocker while also retaining auditable secondary gaps such as missing description, location, compensation, work model, or authoritative URL.

## Funnel semantics

The existing 15-stage output remains available, but it is no longer presented as one fully nested funnel.

Each metric is labeled as either:

* `conversion`, when the numerator is a nested subset of the denominator
* `population`, when the stage is an independent or non-nested portfolio population

A conversion is emitted only when the observed job keys are a subset of the denominator job keys. No displayed conversion can exceed 100 percent.

## Dashboard and GitHub summary

The Dashboard verification section and GitHub Step Summary now show:

* Overall actionable classification and reasons
* Actionable role counts
* Actionable service-level breaches
* Manual intervention count
* Dismissed and deferred exclusions
* Portfolio evidence coverage
* Primary blockers
* Secondary gaps
* Blocker ownership
* Corrected conversion and population metrics

## Compatibility

Sprint 50 does not change the canonical `Jobs` schema, workbook tab ownership, scoring weights, source inventory, or follow-up thresholds.

The deterministic verification run identifier and existing `Runs` schema remain unchanged.

## Validation

Required validation before merge:

```text
python -m compileall -q src tests
pytest
python -m src.workflow_validation
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

The `Regression readiness` pull request workflow runs the full test suite and the gold-standard regression evaluation.

After merge, manually dispatch `Job Tracker Verification Health` in `run` mode and confirm:

1. The workflow succeeds.
2. Dismissed and terminal roles are excluded from actionable counts.
3. Deferred roles with future follow-up dates are excluded.
4. Deferred roles due now are included.
5. No conversion exceeds 100 percent.
6. Dashboard and GitHub summary show actionable health and portfolio coverage separately.
7. The `Runs` record is written idempotently.
