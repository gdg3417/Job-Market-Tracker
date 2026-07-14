# Sprint 49: Generated Surface Consistency

## Objective

Sprint 49 makes every generated workbook surface derive from the same normalized canonical `Jobs` snapshot and current exclusion decisions.

The sprint addresses two observed production inconsistencies:

1. Blocked roles could remain visible in `Review_Queue` when they had manual review state.
2. Google Sheets formatted dates such as `7/1/26` could be interpreted correctly by weekly context but treated as missing by follow-up aging.

## Canonical ownership

`Jobs` remains the canonical source of truth. Generated worksheets remain read-only presentation surfaces.

The unified refresh reads `Jobs` once, normalizes supported date fields, and passes that snapshot through the generated presentation sequence. Supporting configuration and audit tabs are also read once where practical.

No canonical `Jobs` fields are reordered, removed, or rewritten by this sprint.

## Shared date normalization

`src.sheet_dates` is the shared Google Sheets date adapter.

Supported values include:

* ISO dates
* `m/d/yy`
* `m/d/yyyy`
* Python `date`
* Python `datetime`
* Google Sheets numeric serial dates
* Blank values
* Unknown values, which are preserved rather than destructively converted

The adapter is used by weekly metrics, weekly context, follow-up evaluation, application aging, scheduled follow-ups, and the unified refresh snapshot.

`src.weekly_value_sheet_dates` remains as a compatibility entry point for existing imports and commands, but it delegates to the shared adapter.

## Unified generated surface refresh

Run the full presentation refresh with:

```bash
python -m src.presentation_refresh --refresh --source-run manual
```

Optional arguments:

```bash
python -m src.presentation_refresh \
  --refresh \
  --as-of 2026-07-14 \
  --backfill-weeks 12 \
  --source-run manual-maintenance \
  --governance
```

The deterministic write order is:

1. `Review_Queue`
2. `Follow_Up_Queue`
3. `Weekly_Value`
4. `Weekly_Context`
5. `Dashboard`
6. `Digest`
7. Governance, only when explicitly requested

Each surface returns structured status, rows written, warnings, and errors. One presentation failure does not prevent later surfaces from being attempted. The command returns a nonzero exit status when any surface fails.

The command is safe to rerun. Generated worksheets are rebuilt from canonical data, not incrementally appended.

## Exclusion and status consistency

`src.generated_surface_policy` applies current canonical decisions before generated surfaces are built.

Normal review candidates exclude:

* Blocked companies, including Swooped and consulting firms encoded through the canonical exclusion fields
* Hard-excluded roles
* Too-senior hard exclusions
* Dismissed roles
* Closed roles
* Rejected or withdrawn applications

A non-terminal active application remains visible for follow-up, current context, and dashboard tracking even when a later company preference change would otherwise suppress new leads. This prevents a current application from silently disappearing. It does not restore the role to `Review_Queue` as a normal candidate.

Once lifecycle marks a job closed or expired, the role is removed from `Follow_Up_Queue` and `Weekly_Context` even when the prior active `application_status` remains populated. Dashboard reporting may retain the closed record for historical closure context.

## Surface freshness

The refresh writes a generated `Surface_Status` worksheet with these columns:

* `surface_name`
* `last_successful_refresh`
* `source_run`
* `rows_written`
* `status`
* `warning_or_error`
* `data_as_of_date`
* `last_attempted_at`

The sheet has a normal header row at row 1. No metadata rows are inserted above existing tabular surfaces.

When a surface fails, its prior `last_successful_refresh` value is retained and the current attempt time and error are recorded separately.

## Workflow ownership and concurrency

The following workflows use the shared `job-tracker-workbook-writes` concurrency group:

* Daily ingestion
* Production enrichment
* Weekly value and context refresh
* Sheet governance
* Verification health
* Workbook capacity maintenance

This prevents simultaneous workflows from rewriting generated tabs, writing health sections, or compacting workbook grids.

Each workflow also uses `queue: max`. GitHub can therefore retain multiple pending workbook runs instead of canceling an older pending run when another workflow enters the shared concurrency group.

Daily ingestion and production enrichment finish with the unified presentation refresh. The weekly workflow uses the same command rather than separately refreshing weekly tabs.

## Failure handling

A presentation surface failure has these effects:

* Later surfaces are still attempted.
* `Surface_Status` records the failed surface and concise error.
* The command reports `partial_failure` and exits nonzero.
* Canonical `Jobs` data is not rolled back or modified.
* A rerun rebuilds all presentation surfaces from current canonical data.

If `Weekly_Value` fails, `Weekly_Context` uses the prior readable `Weekly_Value` snapshot and records a warning. This preserves a usable context surface while making the stale dependency explicit.

## Test coverage

Sprint 49 adds regression coverage for:

* All supported Google Sheets date representations
* Non-destructive unknown-date handling
* Follow-up aging from `reviewed_date`
* Application aging from `application_date`
* Explicit due-date overrides
* Blocked Swooped roles
* Consulting-company exclusions
* Dismissed-role suppression
* Closed and rejected-role suppression
* Closed roles with stale active application statuses
* Active application preservation
* Deterministic refresh order
* Idempotent reruns
* Partial generated-surface failure
* Surface freshness retention after failure
* Shared workbook-write concurrency
* Queued pending workbook runs

The existing full test suite, regression readiness checks, and gold-standard evaluation remain required before merge.

## Post-merge live validation

After the Sprint 49 pull request is merged:

1. Manually run `Job Tracker Weekly Value Refresh` with `backfill_weeks` set to `12`, `as_of` blank, and `apply_governance` set to `false`.
2. Confirm the workflow summary reports zero failed surfaces and that `Surface_Status` was written.
3. Confirm `Surface_Status` contains successful rows for `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, and `Digest`.
4. Confirm Swooped and consulting-company exclusions do not appear as normal `Review_Queue` candidates.
5. Confirm dismissed and closed roles are absent from current review and follow-up surfaces.
6. Confirm active Topgolf and Toyota records show the same dates, aging, and current status in `Follow_Up_Queue`, `Weekly_Context`, and `Dashboard` where applicable.
7. Rerun the same workflow and confirm row counts remain stable and no duplicate canonical records are created.
8. Run the daily workflow once and confirm the presentation refresh completes after ingestion.
9. Run production enrichment once and confirm the final generated surfaces reflect the enriched canonical rows.
10. Run verification health and confirm it waits for other workbook writers rather than overlapping them.

Do not edit `Review_Queue`, `Follow_Up_Queue`, `Weekly_Value`, `Weekly_Context`, `Dashboard`, `Digest`, or `Surface_Status` directly. Manual review and application changes belong in `Jobs`.

## Deferred scope

Sprint 49 does not redesign health scoring, funnel semantics, source quality, or documentation for the entire tracker. Those remain assigned to Sprints 50 through 52.
