# Sprint 51: Source Quality and Yield Optimization

## Objective

Sprint 51 reduces repeated static-source failures, enforces recoverable retry cooldowns in the normal static-page run, and measures recent source value without changing scoring weights.

The implementation is conservative. It does not disable a source from one failure, delete source history, automatically remove a low-yield search, or treat unavailable search attribution as zero yield.

## Source audit classifications

`python -m src.source_quality_report` classifies the same active static-source population used by normal ingestion as one of:

* `healthy`
* `empty_but_valid`
* `redirect_required`
* `replaced_by_structured_ats`
* `temporarily_blocked`
* `authentication_or_bot_protection`
* `permanent_404_or_retired`
* `dns_failure`
* `manual_review_required`

The live audit performs one bounded HTTP request per source. It follows HTTP redirects but does not crawl discovered job links.

Structured ATS detection covers Greenhouse, Lever, Ashby, and SmartRecruiters. Detection requires an exact ATS domain, explicit configured ATS metadata, or a platform-specific page signature. Ordinary words such as `leverage` do not identify Lever.

A redirect is eligible for automatic URL replacement only when the successful destination also exposes a visible job signal or a supported structured ATS. A redirect to a generic homepage, login page, or unrelated destination remains `manual_review_required`.

## Retry and recovery policy

### Permanent 404 or retired

One 404 observation is insufficient for retirement. The first observation receives a seven-day cooldown.

Two consecutive 404 or 410 observations for the same company ID and source URL require a configuration change before another static retry. A successful source run resets the failure streak.

### DNS failure

DNS failures receive a seven-day cooldown. Three consecutive DNS failures require manual URL review and a longer cooldown. A later successful run resets the DNS failure streak.

### HTTP 403, authentication, or bot protection

Protected responses remain recoverable. They receive a fourteen-day cooldown and are not treated as permanent failures.

### Rate limits and temporary server failures

Rate limits and temporary failures receive a one-day cooldown initially and a seven-day cooldown after repeated consecutive failures.

### Empty but valid

A reachable source with no current job signal remains enabled and receives a fourteen-day reduced-cadence interval.

## Daily static-page enforcement

The normal static-page ingestion command reads the latest `Source_Audit` rows before making source requests.

Matching uses the exact combination of:

* `Config_Companies.company_id`
* normalized configured `source_url`

The daily run skips a source when:

* its cooldown has not expired
* a configuration change is required
* manual review is required
* a validated redirect or structured ATS conversion is waiting for review

Expired temporary cooldowns become eligible again automatically. Healthy sources continue normally. When all configured static sources are skipped, the run reports `all_sources_in_cooldown` rather than a source failure.

The weekly source-quality audit refreshes the policy evidence. A manual report-mode run can refresh it sooner when investigating a new failure.

## Explicit configuration updates

The source-quality workflow supports two modes:

* `report`
* `apply_reviewed_cleanup`

`apply_reviewed_cleanup` requires exact `Config_Companies.company_id` values. The implementation then also verifies that the current configured source URL exactly matches the audited source URL before changing the row.

Supported changes are limited to:

1. Replace an obsolete URL with a validated career-page redirect destination.
2. Move a validated Greenhouse or Lever source to its structured ingestion mode.
3. Mark a consecutively confirmed permanent 404 source inactive and manual-review-only.

Temporary failures, protected pages, and DNS failures below the review threshold are not disabled.

Before an approved configuration mutation, the workflow writes the detailed `Source_Audit` and `Source_Yield` evidence. Each applied update records:

* original source URL
* final source URL
* classification and action
* prior configuration values
* resulting configuration values
* observation time

Existing configuration notes are preserved. Historical `Runs` and `Source_Health` records are not deleted or rewritten.

## Source-yield report

The default reporting period is four weeks and can be changed with `--weeks`.

The generated `Source_Yield` worksheet reports:

* leads received
* jobs accepted
* auto-rejected leads
* blocked-company rejects
* too-junior rejects
* too-senior rejects
* surfaced-for-review count
* manually dismissed count
* interested count
* applied count
* strong-fit count
* stretch-fit count
* average potential score
* review yield
* actionable conversion

Rows are grouped by available evidence across Gmail alert evidence, static company source, ATS platform, company, and source type.

Unique job or rejection identities are used within each group so duplicate lineage rows do not inflate counts. Review yield uses only positive outcomes within the surfaced population, so the percentage cannot exceed 100 percent.

### Configured search attribution limitation

Accepted Gmail jobs currently retain generic Gmail lineage but do not retain `Config_Searches.search_id`. The system therefore cannot accurately assign accepted jobs to an individual configured search.

Active configured searches are still inventoried, but they receive:

* recommendation: `attribution_unavailable`
* no zero-yield interpretation
* no narrow, reduce-cadence, or retirement recommendation

Subject-level Gmail evidence remains visible separately. Search-level optimization must wait until durable search-ID lineage is added to ingestion.

### Zero-result sources

Active static company sources with no observed leads receive an advisory `review_or_reduce_cadence` recommendation. Strategic target-company sources receive `keep_strategic_coverage` instead.

No recommendation changes configuration automatically.

## Generated worksheets

Sprint 51 creates two generated, read-only surfaces:

* `Source_Audit`
* `Source_Yield`

The workflow replaces their generated contents idempotently and applies standard sheet governance. Headers are gray, filters are enabled, and both surfaces are included in `Sheet_Guide`. Neither worksheet is a canonical data-entry surface.

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

Apply one explicitly approved configuration update:

```text
python -m src.source_quality_report --write-report --weeks 4 --approved-company-id example_company
```

Repeat `--approved-company-id` for multiple exact company identifiers. Exact source-URL matching is still required internally.

## GitHub Actions

`Job Tracker Source Quality` runs weekly and supports manual dispatch.

The workflow:

1. Runs the full test suite.
2. Migrates and validates the workbook schema.
3. Audits current static sources.
4. Builds four-week source yield.
5. Inventories actual zero-result static sources.
6. Marks configured searches with unavailable attribution separately.
7. Writes `Source_Audit` and `Source_Yield` before approved configuration mutations.
8. Applies only explicitly approved and exact-source-matched cleanup.
9. Applies generated-surface governance.
10. Appends a `Runs` record.
11. Writes classifications, recommendations, attribution limitations, and applied changes to the GitHub Step Summary.

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
3. Confirm report mode changed no `Config_Companies` rows.
4. Review permanent 404, DNS, protected, redirect, ATS, and manual-review classifications.
5. Confirm temporary and empty-valid sources have future retry dates.
6. Confirm configured searches show `attribution_unavailable`, not zero-yield retirement advice.
7. Confirm strategic target-company sources retain coverage.
8. Back up the workbook.
9. Identify exact company IDs and exact current source URLs for reviewed cleanup candidates.
10. Rerun in `apply_reviewed_cleanup` mode only for approved company IDs.
11. Confirm the Step Summary reports the original and final source URLs for each applied change.
12. Run the normal static-page workflow.
13. Confirm sources in active cooldown are listed under `source_policy_skips` and are not requested.
14. Confirm eligible healthy or expired-cooldown sources still run.
15. Confirm reviewed permanent 404 rows no longer qualify as active static sources.

## Scope boundaries

Sprint 51 does not change scoring weights, canonical `Jobs` fields, Gmail ingestion schema, or automatic search configuration. Durable `Config_Searches.search_id` lineage for accepted Gmail jobs remains future work.

Complete system documentation and maintenance-readiness consolidation remain Sprint 52 scope.
