# Sprint 51: Source Quality and Yield Optimization

## Objective

Sprint 51 reduces repeated static-source failures and makes source and search value measurable over a configurable reporting window.

The implementation is conservative. It does not disable a source from one failure, delete source history, change scoring weights, or automatically remove a low-yield search.

## Source audit classifications

`python -m src.source_quality_report` classifies active static and career-page sources as one of:

* `healthy`
* `empty_but_valid`
* `redirect_required`
* `replaced_by_structured_ats`
* `temporarily_blocked`
* `authentication_or_bot_protection`
* `permanent_404_or_retired`
* `dns_failure`
* `manual_review_required`

The live audit follows redirects and performs one bounded HTTP request per audited source. It does not crawl unrestricted pages or follow discovered job links.

Structured ATS detection covers Greenhouse, Lever, Ashby, and SmartRecruiters. When one is detected, the report recommends the structured ATS path instead of generic static-page parsing.

## Retry policy

### Permanent 404 or retired

One observation is not enough to retire a source. The first 404 receives a seven-day cooldown.

A repeated 404, 410, or corroborating historical failure requires a configuration change before another static retry. The source is not changed automatically.

### DNS failure

DNS failures receive a seven-day cooldown. After three observations, the source requires manual URL review and a longer cooldown.

### HTTP 403, authentication, or bot protection

A protected response remains recoverable. It receives a fourteen-day cooldown and is not treated as permanently dead from one observation.

### Rate limits and temporary server failures

HTTP 429 receives a one-day cooldown. Other temporary failures receive one day initially and seven days after repeated failures.

### Empty but valid

A reachable source without current job signals remains enabled and is recommended for reduced cadence rather than immediate retirement.

## Explicit configuration updates

The source-quality workflow supports two modes:

* `report`
* `apply_reviewed_cleanup`

`apply_reviewed_cleanup` requires exact `Config_Companies.company_id` values. Only supported, evidence-backed changes are applied:

1. Replace an obsolete URL with a validated redirect destination.
2. Move Greenhouse or Lever sources to the structured ingestion mode.
3. Mark a repeated permanent 404 source inactive and manual-review-only.

Temporary failures, DNS failures below the limit, and protected pages are not disabled.

Each approved update appends a Sprint 51 marker to `Config_Companies.notes`. Existing notes and historical `Runs` and `Source_Health` records remain unchanged.

## Source-yield report

The default reporting period is four weeks and can be changed with `--weeks`.

The generated `Source_Yield` worksheet reports:

* Leads received
* Jobs accepted
* Auto-rejected leads
* Blocked-company rejects
* Too-junior rejects
* Too-senior rejects
* Surfaced-for-review count
* Manually dismissed count
* Interested count
* Applied count
* Strong-fit count
* Stretch-fit count
* Average potential score
* Review yield
* Actionable conversion

Rows are grouped by available evidence across Gmail alert or search, static company source, ATS platform, company, and source type.

The complete report also inventories active configured sources and searches with no observed leads during the reporting window. Zero-result configurations receive an advisory review recommendation. Strategic target-company sources receive `keep_strategic_coverage` instead of a retirement recommendation.

Unique job or rejection identities are used within each group so duplicate source rows do not inflate counts.

## Yield recommendations

Recommendations are advisory only:

* `keep`
* `keep_strategic_coverage`
* `narrow_search`
* `narrow_or_retire`
* `reduce_cadence`
* `review_filtering`
* `review_or_reduce_cadence`

A single poor week never disables a source. Strategic target-company coverage is retained unless a replacement source is validated.

## Generated worksheets

Sprint 51 creates two generated, read-only surfaces:

* `Source_Audit`
* `Source_Yield`

The workflow replaces the generated contents idempotently and then applies the standard sheet-governance policy. The headers are gray, filters are enabled, and the surfaces are added to `Sheet_Guide`. Neither worksheet is a canonical data-entry surface.

## Commands

Calculate without workbook writes:

```text
python -m src.source_quality_report --dry-run --weeks 4
```

Write the source audit and complete yield report:

```text
python -m src.source_quality_report --write-report --weeks 4
python -m src.sheet_governance --apply
```

Run without live HTTP probes:

```text
python -m src.source_quality_report --write-report --weeks 4 --skip-live-probes
```

Apply one or more explicitly approved configuration updates:

```text
python -m src.source_quality_report --write-report --weeks 4 --approved-company-id example_company
```

Repeat `--approved-company-id` for multiple exact company identifiers.

## GitHub Actions

`Job Tracker Source Quality` runs weekly and supports manual dispatch.

The workflow:

1. Runs the full test suite.
2. Migrates and validates the workbook schema.
3. Audits current static sources.
4. Writes `Source_Audit` and `Source_Yield`.
5. Includes active zero-result configurations.
6. Applies only explicitly approved cleanup when requested.
7. Applies generated-surface governance.
8. Appends a `Runs` record.
9. Writes classification, yield, zero-result, and update counts to the GitHub Step Summary.

All workbook writes use the shared `job-tracker-workbook-writes` concurrency group.

## Validation

Before merge:

```text
python -m compileall -q src tests
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

After merge:

1. Manually run `Job Tracker Source Quality` in `report` mode with a four-week window.
2. Confirm `Source_Audit` and `Source_Yield` are created or refreshed.
3. Confirm permanent 404, DNS, protected, redirect, and ATS classifications are reasonable.
4. Confirm temporary failures remain retryable.
5. Confirm active zero-result sources and searches are visible.
6. Confirm strategic target-company sources are retained.
7. Confirm no source configuration changed in report mode.
8. Review recommended changes and identify exact company IDs that are safe to update.
9. Create a workbook backup before applying reviewed cleanup.
10. Rerun in `apply_reviewed_cleanup` mode with only approved company IDs.
11. Confirm reviewed repeated permanent 404 sources are no longer active static sources.
12. Confirm temporary failures and strategic target-company sources remain recoverable.
13. Run the normal daily workflow and confirm source-failure noise and runtime do not regress.

## Scope boundaries

Sprint 51 does not change automatic scoring weights, delete low-yield searches, disable a source from one observation, add paid APIs, perform unrestricted web crawling, delete source-health history, or change canonical `Jobs` fields.

Complete system documentation and maintenance-readiness consolidation remain Sprint 52 scope.
