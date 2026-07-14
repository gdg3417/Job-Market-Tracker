# Sprint 47 Workbook Capacity Hotfix

## Root cause

Google Sheets represents an open-ended structural range by omitting `endRowIndex` or `endColumnIndex`. The initial Sprint 47 implementation treated either omission as an unknown range that blocked both row and column compaction.

The production `Jobs` conditional-format rules are open-ended by row but bounded to the normal scoring columns. Those rules should prevent trailing row deletion, but they should not prevent safe deletion of thousands of unused columns.

## Fix

The hotfix adds a compatibility layer that materializes an omitted range end at the current grid boundary before running the existing Sprint 47 audit. This preserves the original conservative behavior for the affected dimension while allowing the independent dimension to be evaluated normally.

Examples:

- Missing `endRowIndex`, bounded columns: row compaction is blocked, column compaction remains eligible.
- Missing `endColumnIndex`, bounded rows: column compaction is blocked, row compaction remains eligible.
- Missing both ends: both dimensions remain blocked.

The original workbook-capacity module remains unchanged. The workflow routes through the hotfix module, which delegates to the existing audit, formatting inspection, compaction, reporting, and threshold logic.

## Production safety

- Canonical `Jobs` width remains protected at 135 columns plus the existing two-column headroom.
- Populated cells, formulas, notes, validations, hyperlinks, chips, custom dimensions, filters, merges, conditional formats, protections, named ranges, charts, slicers, and frozen panes remain preservation boundaries.
- Scheduled runs remain read-only.
- Applying compaction still requires `apply_compaction=true`.
- Removing blank or unverified formatting still requires `allow_trim_blank_formatting=true`.
- A shifted record outside the canonical range remains a hard boundary and prevents deletion through that record.

## Post-merge validation

1. Run `Job Tracker Workbook Capacity` with both inputs set to `false`.
2. Confirm `Jobs` proposes at least 137 columns and reports only row compaction as blocked by the open-ended conditional-format rules.
3. Create a fresh workbook backup.
4. Run the workflow with both inputs set to `true`.
5. Confirm capacity falls below 80 percent and workbook controls remain intact.
6. Rerun with both inputs set to `true` and confirm zero additional requests and zero reclaimed cells.
7. Run `Job Tracker Sheet UX Governance` once after compaction.
