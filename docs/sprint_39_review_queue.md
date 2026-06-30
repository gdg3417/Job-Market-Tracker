# Sprint 39: Review UX and Sheet Usability Optimization

## Purpose

Sprint 39 adds a review-facing `Review_Queue` worksheet generated from the canonical `Jobs` worksheet. The goal is to make daily role review practical without changing the Jobs schema order or turning the review table into a second source of truth.

## Tab roles

| Tab | Purpose | Edit guidance |
| --- | --- | --- |
| `Dashboard` | Executive summary, action queue, tracker health, source health, and top roles to review. | Do not edit generated content. Refresh with `python -m src.dashboard`. |
| `Review_Queue` | Operational review surface with identity, priority, evidence, review, follow-up, application, and source fields next to each other. | Read-only in Sprint 39. Use it to filter and review, then update canonical review fields in `Jobs`. |
| `Jobs` | Canonical database table used by ingestion, scoring, enrichment, Dashboard, Digest, and readiness workflows. | Safe place to update manual review fields. Do not reorder columns. |

## Review_Queue design

`Review_Queue` is generated from `Jobs` and includes:

| Group | Fields |
| --- | --- |
| Identity | `job_key`, `company`, `title`, `location`, `canonical_url` |
| Priority | `potential_priority`, `potential_priority_score`, `score_status`, `evidence_completeness_score` |
| Enrichment | `enrichment_status`, `enrichment_match_confidence`, `manual_authoritative_url` |
| Decision evidence | `move_value_classification`, `move_value_notes`, `work_model`, `base_salary_min`, `base_salary_max`, `compensation_source_type`, `commute_bucket` |
| Review | `review_status`, `reviewed_date`, `interest_decision`, `manual_priority`, `manual_fit_rating`, `review_notes` |
| Follow-up | `next_action`, `next_action_date`, `follow_up_date` |
| Application | `application_status`, `application_date`, `resume_version`, `referral_or_contact` |
| Source reference | `source_primary`, `source_job_id` |

## Inclusion rules

`Review_Queue` includes rows that are useful for review or audit:

1. Any row with manual review state, including application tracking, notes, manual priority, manual authoritative URL, or dismissal details.
2. Rows with active review statuses such as `review_now`, `reviewing`, `interested`, `watch`, `deferred`, `applied`, `interviewing`, or `offer`.
3. Rows with `potential_priority` of `high` or `medium`.
4. Rows with `score_status` of `verified` or `partially_verified`.
5. Rows with enrichment work or enrichment problems, including `pending`, `in_progress`, `partial`, `not_found`, `ambiguous`, `retryable_failure`, or `permanent_failure`.
6. Other open rows with `total_score` of at least 50.

Terminal low-value rows without review state are excluded. Dismissed rows remain visible for audit when they have review state.

## Sorting

The generated queue is sorted by:

1. `manual_priority` descending, with blanks last.
2. Review action status, with `review_now` first.
3. `potential_priority_score` descending.
4. `evidence_completeness_score` descending.
5. `next_action_date` ascending when present.
6. Company and title as stable tie breakers.

## Filter and freeze behavior

The refresh command applies:

| Tab | Freeze | Filter |
| --- | --- | --- |
| `Review_Queue` | Row 1 and columns A through E | Basic filter across the populated review table |
| `Jobs` | Row 1 and columns A through D | Basic filter across the populated Jobs range |

The key fields to filter in `Review_Queue` are present as first-class columns:

`review_status`, `interest_decision`, `manual_priority`, `potential_priority`, `score_status`, `enrichment_status`, `move_value_classification`, `application_status`, `next_action_date`, `company`, `title`, `location`, `work_model`, `compensation_source_type`, and `commute_bucket`.

Programmatic filter views are intentionally deferred. The Google Sheets API can create filter views, but robust filter-view setup is brittle when the workbook already has user-created filters. Use the basic filter first, then save manual filter views in the Sheet if needed.

## Recommended manual filter views

Create these manually from `Review_Queue` if useful:

| Filter view | Suggested criteria |
| --- | --- |
| `1 - Review Now` | `review_status` is `not_reviewed`, `review_now`, or blank for visible high-potential rows |
| `2 - High Potential` | `potential_priority` equals `high` |
| `3 - Partial Evidence` | `score_status` equals `partially_verified` |
| `4 - Enrichment Problems` | `enrichment_status` is `not_found`, `ambiguous`, `retryable_failure`, or `permanent_failure` |
| `5 - Compensation Follow-up` | `compensation_source_type` is blank or `unknown` |
| `6 - Interested and Watch` | `interest_decision` is `interested` or `watch` |
| `7 - Applied Pipeline` | `application_status` is `applied`, `interviewing`, or `offer` |
| `8 - Dismissed Audit` | `review_status` equals `dismissed` |

## Daily review workflow

1. Refresh the workbook outputs.
2. Open `Review_Queue`.
3. Filter to `potential_priority = high`, `score_status = partially_verified`, or enrichment problem statuses.
4. Review identity and evidence fields without horizontal scrolling.
5. Update decisions in `Jobs`, not `Review_Queue`.
6. Refresh `Review_Queue` after editing `Jobs`.

## Safe fields to edit in Jobs

The following manual fields are safe to edit in `Jobs`:

| Purpose | Jobs fields |
| --- | --- |
| Review decision | `review_status`, `reviewed_date`, `reviewer`, `interest_decision`, `manual_priority`, `manual_fit_rating`, `review_notes` |
| Manual URL correction | `manual_authoritative_url` |
| Dismissal | `dismissal_reason`, `dismissal_detail` |
| Follow-up | `follow_up_date`, `next_action`, `next_action_date` |
| Application tracking | `application_status`, `application_date`, `application_url`, `resume_version`, `cover_letter_version`, `referral_or_contact`, `interview_stage`, `last_application_update` |

Do not edit generated scoring, enrichment, lifecycle, or source fields unless a future sprint adds a safe sync flow.

## Updating application status

Use `Review_Queue` to find the role, then update the matching `job_key` row in `Jobs`:

1. Set `interest_decision` to `interested` or `applied`.
2. Set `application_status` to `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`, or `closed`.
3. Set `application_date` when an application is submitted.
4. Add `resume_version` and `referral_or_contact` when applicable.
5. Use `next_action` and `next_action_date` for follow-up.
6. Refresh `Review_Queue`.

## Handling manual authoritative URLs

If enrichment fails or matches the wrong page:

1. Filter `Review_Queue` to enrichment problem statuses.
2. Open the company or ATS posting manually.
3. Copy the authoritative job posting URL.
4. Update `manual_authoritative_url` in `Jobs` for the matching `job_key`.
5. Refresh `Review_Queue`.
6. Run the normal enrichment or readiness flow when needed.

## Refresh commands

Run from PowerShell:

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"

.\.venv\Scripts\Activate.ps1

python -m src.schema --migrate
python -m src.schema --validate
python -m src.dashboard --no-run-log
python -m src.review_queue --refresh
```

## Sprint 39 production validation

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"

git switch main
git pull --ff-only origin main
git fetch origin codex/sprint-39-review-queue
git switch codex/sprint-39-review-queue

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

pytest
python -m src.schema --migrate
python -m src.schema --validate
python -m src.workflow_validation
python -m src.dashboard --no-run-log
python -m src.review_queue --refresh
python -m src.production_readiness --evaluate-readiness --write-run
```

Confirm in Google Sheets:

1. `Review_Queue` exists.
2. `Review_Queue` has frozen row 1 and frozen columns A through E.
3. `Jobs` has frozen row 1 and frozen columns A through D.
4. Both `Review_Queue` and `Jobs` have working filters.
5. The following rows are visible in `Review_Queue` if present in current Jobs data:
   1. Topgolf, `Sr Manager, Strategic Planning`
   2. Osteal Therapeutics, `Director, Commercial Operations`
   3. Toyota North America, `National Manager, Product`
   4. divcon, `Director of Product Strategy`
   5. Deloitte, `Strategic Planning Manager`
6. Manual review fields in `Jobs` remain intact after refreshing `Review_Queue`.

## Implementation decision

Sprint 39 uses Option A: `Review_Queue` is read-only/reporting only.

Two-way sync is deferred because a generated review surface that accepts edits can overwrite manual review fields if blank cells, filters, duplicate `job_key` values, or stale rows are mishandled. A future safe sync should use a separate `Manual_Review` input tab or explicit command that validates `job_key`, allowed statuses, duplicate keys, and blank-overwrite behavior before writing back to `Jobs`.

## Remaining limitations

1. Programmatic filter views are not created in Sprint 39.
2. Manual edits still happen in `Jobs`.
3. `Review_Queue` must be refreshed after manual Jobs edits.
4. Two-way sync is intentionally deferred until it can be tested against duplicate keys and blank overwrite cases.
