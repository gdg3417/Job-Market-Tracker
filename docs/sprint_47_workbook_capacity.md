# Sprint 47 Workbook Capacity and Grid Safety

## Objective

Sprint 47 adds a conservative workbook capacity audit, an explicit trailing grid compaction command, and permanent warning and critical thresholds. It does not change the canonical `Jobs` schema or run destructive maintenance during normal daily workflows.

## Production baseline

The pre-implementation live workbook inspection on July 13, 2026 found:

* 9,924,994 allocated cells, or 99.25 percent of the Google Sheets limit
* 9,305 allocated columns on `Jobs`
* 135 canonical `Jobs` fields, through column `EE`
* Repeated gray header formatting and white body formatting extending into far-right blank `Jobs` columns

The repeated formatting explains why formatting-only columns require a separate explicit approval. Values, formulas, notes, validation, hyperlinks, structural ranges, and other hard evidence remain non-removable.

## Components

### Capacity audit

Run a read-only audit:

```powershell
python -m src.workbook_capacity --audit
```

Run the audit and fail maintenance validation when workbook capacity is at or above 90 percent:

```powershell
python -m src.workbook_capacity --audit --enforce-critical
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
python -m src.workbook_capacity --audit --warning-threshold 0.80 --critical-threshold 0.90
```

## Safe compaction

Preview the conservative compaction plan without changing the workbook:

```powershell
python -m src.workbook_capacity --compact
```

Preview a plan that includes formatting-only or formatting-unverified trailing grid cells:

```powershell
python -m src.workbook_capacity --compact --allow-trim-blank-formatting
```

Apply the approved plan explicitly:

```powershell
python -m src.workbook_capacity --compact --apply --allow-trim-blank-formatting --enforce-critical
```

The apply flag is required. The separate formatting approval flag is also required when candidate trailing rows or columns contain formatting or could not be fully scanned. Normal daily, enrichment, weekly, verification-health, and governance workflows do not invoke compaction.

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
* Row and column safety buffers

A range without a bounded ending row or column is treated as unknown. The worksheet is not compacted even when blank formatting approval is supplied.

The command audits immediately before applying changes, submits trailing `deleteDimension` requests, audits again, and returns a before-and-after report. Rerunning the command after a successful compaction produces no additional requests.

## GitHub Actions workflow

`.github/workflows/workbook-capacity.yml` provides:

* A monthly read-only audit
* Manual read-only audit dispatch
* Manual explicit compaction through the `apply_compaction` input
* Separate approval for formatting-only or formatting-unverified trailing grid cells through `allow_trim_blank_formatting`
* Focused tests and live schema validation before workbook access
* A JSON report artifact retained for 30 days
* A GitHub Step Summary with workbook and per-sheet capacity metrics
* Critical threshold enforcement after the audit or applied compaction

Scheduled runs cannot set either apply input and therefore cannot compact the workbook.

## Test coverage

Focused tests cover:

* Normal-sized workbooks
* Oversized blank grids
* Canonical schema preservation
* Populated cells beyond the canonical schema
* Formulas outside the normal schema
* Boolean false values
* Notes, validation, custom dimensions, and chart anchors
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
pytest tests/test_workbook_capacity.py tests/test_workbook_capacity_workflow.py
```

Run the complete repository validation before merge:

```powershell
pytest
python -m src.schema --validate
python -m src.workflow_validation
```

## Post-merge workbook validation

1. Open **Actions** and select **Job Tracker Workbook Capacity**.
2. Run the workflow with `apply_compaction` set to `false` and `allow_trim_blank_formatting` set to `false`.
3. Review the Step Summary and downloaded JSON report.
4. Confirm that no worksheet has unknown ranges requiring manual inspection.
5. Confirm that `Jobs` proposes a target no lower than the 135 canonical fields plus configured column headroom.
6. Confirm that the report identifies the far-right `Jobs` grid as formatting-only or formatting-unverified and requires approval.
7. Run the workflow again with both `apply_compaction` and `allow_trim_blank_formatting` set to `true`.
8. Confirm that the workflow passes, capacity is below 80 percent, and the before-and-after report shows reclaimed cells.
9. Open the workbook and verify `Jobs`, filters, frozen columns, dropdowns, formatting, manual review data, and all generated tabs.
10. Rerun the workflow with both inputs set to `true` and confirm that zero additional requests are submitted.
11. Run **Job Tracker Sheet UX Governance** once after compaction to revalidate workbook presentation controls.

## Scope boundaries

Sprint 47 does not archive jobs, split the workbook, change scoring, change Gmail ingestion, alter generated surface refresh behavior, or redesign verification health. Those areas remain assigned to later sprints in the maintenance-hardening roadmap.
