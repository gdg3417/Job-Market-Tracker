# Sprint 47 Workbook Capacity and Grid Safety

## Objective

Sprint 47 adds a conservative workbook capacity audit, an explicit trailing grid compaction command, and permanent warning and critical thresholds. It does not change the canonical `Jobs` schema or run destructive maintenance during normal daily workflows.

A post-Sprint 47 hotfix now separates capacity compaction from canonical Jobs write integrity. Capacity remains responsible for preserving suspicious cells and ranges. `src.jobs_integrity` and the bounded Sheets writer are responsible for preventing and detecting out-of-bounds Jobs writes.

## Production baseline

The pre-implementation live workbook inspection on July 13, 2026 found:

* 9,924,994 allocated cells, or 99.25 percent of the Google Sheets limit
* 9,305 allocated columns on `Jobs`
* 135 canonical `Jobs` fields, through column `EE`
* Repeated gray header formatting and white body formatting extending into far-right blank `Jobs` columns

The repeated formatting explains why formatting-only columns require a separate explicit approval. Values, formulas, notes, validation, hyperlinks, structural ranges, and other hard evidence remain non-removable.

A later production incident found `Jobs!LTO680 = insufficient_evidence`. The value was a recognized `move_value_classification` value outside the canonical `A:EE` range. Capacity correctly refused to delete through that coordinate. The user manually preserved and corrected the workbook, then deleted all columns after `EE`. The writer defect required a separate hotfix and must not be treated as a compaction problem.

## Components

### Capacity audit

Run a read-only audit:

```powershell
python -m src.workbook_capacity_hotfix --audit
```

Run the audit and fail maintenance validation when workbook capacity is at or above 90 percent:

```powershell
python -m src.workbook_capacity_hotfix --audit --enforce-critical
```

The audit reports the following for every worksheet:

* Allocated rows, columns, and cells
* Sheet role, including canonical data, generated data, configuration, system ledger, or unknown
* Canonical column count where applicable
* Highest populated row and column
* Highest formula row and column
* Highest row and column containing hard metadata such as notes, validation, hyperlinks, custom dimensions, chart anchors, or structural ranges
* Highest sampled or fully scanned row and column containing formatting
* Populated, formula, note, validation, and detected formatted blank cell counts
* Structural ranges, including filters, merges, conditional formatting, protected ranges, banding, and named ranges
* Proposed target dimensions
* Cells reclaimable with explicit formatting approval
* Cells reclaimable without formatting approval
* Truly unused cells where a complete clean formatting scan proves the trailing region is unformatted
* Formatting-only or formatting-unverified cells requiring approval
* Unknown or unbounded ranges requiring manual inspection

Workbook-wide output includes allocated cells, estimated reclaimable cells, percentage of the 10,000,000 cell limit consumed, and the resulting health classification.

### Jobs integrity audit

Capacity does not replace the canonical Jobs integrity scanner.

Run:

```powershell
python -m src.jobs_integrity --audit
python -m src.jobs_integrity --audit --enforce
```

The Jobs audit requires exact canonical headers, grid width 135, final header `decision_evidence_conflict_notes`, and zero values, formulas, hard cell metadata, or structural metadata after `EE`.

The scanner has no repair or deletion mode. See `docs/JOBS_WRITE_BOUNDARY_INTEGRITY.md`.

### Bounded metadata reads

The audit uses a filtered Google Sheets metadata request that excludes cell formatting from the core safety scan. This avoids loading millions of formatting-only cells from an oversized worksheet while still reading values, formulas, notes, validation, hyperlinks, custom dimensions, charts, slicers, filters, protected ranges, and named ranges.

Formatting is inspected separately only in candidate trailing ranges. Smaller ranges are fully scanned. Larger ranges are sampled and therefore require explicit approval before removal.

### Thresholds

Default thresholds are:

* Warning at 80 percent
* Critical at 90 percent

Warnings do not cause destructive action. A critical result fails only when `--enforce-critical` is supplied.

Custom thresholds are supported for controlled testing:

```powershell
python -m src.workbook_capacity_hotfix --audit --warning-threshold 0.80 --critical-threshold 0.90
```

## Safe compaction

Preview the conservative compaction plan without changing the workbook:

```powershell
python -m src.workbook_capacity_hotfix --compact
```

Preview a plan that includes formatting-only or formatting-unverified trailing grid cells:

```powershell
python -m src.workbook_capacity_hotfix --compact --allow-trim-blank-formatting
```

Apply the approved plan explicitly:

```powershell
python -m src.workbook_capacity_hotfix --compact --apply --allow-trim-blank-formatting --enforce-critical
```

The apply flag is required. The separate formatting approval flag is also required when candidate trailing rows or columns contain formatting or could not be fully scanned. Normal daily, enrichment, weekly, verification-health, and governance workflows do not invoke compaction.

For `Jobs`, the approved steady-state grid width is exactly 135 columns. Blank trailing grid can be proposed for deletion through `EE`. Populated, formula, hard metadata, or structural evidence after `EE` remains a preservation boundary and blocks automatic cleanup.

Compaction removes only trailing rows or columns beyond the calculated preservation boundary. The preservation boundary includes:

* All canonical columns
* All populated cells, including boolean false and numeric zero values
* All formulas
* All notes
* All data validation
* Hyperlinks, rich text runs, and smart chip metadata
* Custom row or column dimension metadata
* Embedded chart and slicer anchors
* Filters, merges, conditional formatting, banded ranges, protected ranges, and named ranges
* Frozen row and column requirements
* Row safety buffers

A range without a bounded ending row or column is treated as unknown. The worksheet is not compacted even when blank formatting approval is supplied.

The command audits immediately before applying changes, submits trailing `deleteDimension` requests, audits again, and returns a before-and-after report. Rerunning the command after a successful compaction produces no additional requests.

Capacity must never delete a suspicious out-of-bounds Jobs cell merely to restore width. Preserve the evidence, identify the writer defect, correct the displaced data with reviewed evidence, and run the Jobs integrity audit before compaction.

## GitHub Actions workflow

`.github/workflows/workbook-capacity.yml` provides:

* A monthly read-only audit
* Manual read-only audit dispatch
* Manual explicit compaction through the `apply_compaction` input
* Separate approval for formatting-only or formatting-unverified trailing grid cells through `allow_trim_blank_formatting`
* Focused tests and live schema validation before workbook access
* A pre-run Jobs integrity JSON capture
* Post-run Jobs integrity enforcement
* A JSON report artifact retained for 30 days
* A GitHub Step Summary with workbook and per-sheet capacity metrics
* Critical threshold enforcement after the audit or applied compaction

Scheduled runs cannot set either apply input and therefore cannot compact the workbook.

## Test coverage

Focused tests cover:

* Normal-sized workbooks
* Oversized blank grids
* Exact 135-column Jobs steady state
* Canonical schema preservation
* Populated cells beyond the canonical schema
* The historical `LTO680 = insufficient_evidence` fixture
* Displaced whole or partial rows
* Formulas outside the normal schema
* Boolean false values
* Notes, validation, hyperlinks, custom dimensions, and chart anchors
* Formatting-only trailing ranges
* Explicit formatting approval
* Complete clean formatting scans
* Unbounded ranges requiring manual inspection
* Explicit apply behavior
* Idempotent compaction
* Warning and critical thresholds
* Workflow protection against scheduled destructive action

Run focused tests:

```powershell
pytest tests/test_workbook_capacity.py tests/test_workbook_capacity_hotfix.py tests/test_workbook_capacity_workflow.py tests/test_jobs_integrity.py
```

Run the complete repository validation before merge:

```powershell
python -m compileall -q src tests
pytest
python -m src.jobs_write_contract --audit --enforce
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
```

## Post-merge workbook validation

The manually repaired workbook should begin at exactly 135 Jobs columns.

1. Run `python -m src.jobs_integrity --audit --enforce` through the production workflow environment.
2. Confirm canonical headers 135, grid columns 135, and zero out-of-bounds values, formulas, hard metadata, and structural metadata.
3. Run **Job Tracker Sheet UX Governance**.
4. Confirm governance passes and Jobs remains 135 columns.
5. Run **Job Tracker Daily Run** in normal Gmail mode.
6. Confirm new and duplicate jobs remain inside `A:EE`, the post-write audit passes, and Jobs remains 135 columns.
7. Allow the triggered **Job Tracker Enrichment Run** to complete.
8. Confirm its pre-write and post-write audits pass and Jobs remains 135 columns.
9. Allow **Job Tracker Verification Health** to complete and confirm its read-only Jobs preflight passes.
10. Run **Job Tracker Workbook Capacity** with `apply_compaction = false` and `allow_trim_blank_formatting = false`.
11. Confirm no compaction requests, no out-of-bounds preservation boundary, Jobs width 135, and workbook capacity below 80 percent.
12. Recheck after the next scheduled Daily and Enrichment chain.

Do not manually modify the workbook during this validation sequence.

## Scope boundaries

Sprint 47 and the write-boundary hotfix do not archive jobs, split the workbook, change scoring, change Gmail parsing, alter generated surface semantics, or redesign verification health. The hotfix does not automatically repair, delete, or compact out-of-bounds evidence.
