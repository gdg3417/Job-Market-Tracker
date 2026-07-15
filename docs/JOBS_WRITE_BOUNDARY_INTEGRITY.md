# Jobs Write Boundary Integrity

## Canonical boundary

`Jobs` contains exactly the fields in `src.models.JOB_FIELDS`. Production logic derives the width from that tuple.

Current contract:

* Canonical fields: 135
* Canonical range: `A:EE`
* Final field: `decision_evidence_conflict_notes`
* Preferred and approved grid width: 135 columns
* Populated, formula, note, validation, hyperlink, chip, rich-text, chart, slicer, filter, protected-range, named-range, or other hard metadata boundary after `EE`: prohibited

Normal workflows cannot expand the `Jobs` grid. Only an intentional append-only schema migration may increase the canonical width, and the migration may expand only to the exact new `len(JOB_FIELDS)` value.

## Bounded writes

All canonical `Jobs` rows are serialized in `JOB_FIELDS` order and written to an explicit range:

```text
Jobs!A<row>:EE<row>
```

The writer rejects missing fields, unknown fields, malformed headers, reordered headers, row widths other than 135, ranges ending after `EE`, and Google Sheets API requests using zero-based column index 135 or greater.

New job placement is calculated from canonical identity fields inside `Jobs!A:EE`. A value in a distant column cannot change the append target. The actual written row is returned to the upsert cache so later duplicate updates remain on the same canonical row.

## Read-only audit

Run:

```powershell
python -m src.jobs_integrity --audit
```

Enforce a nonzero exit when unsafe:

```powershell
python -m src.jobs_integrity --audit --enforce
```

The scanner reports:

* Canonical and actual header counts
* Grid row and column counts
* Highest populated row and column
* Out-of-bounds value count
* Out-of-bounds formula count
* Out-of-bounds hard cell metadata count
* Out-of-bounds structural metadata count
* First limited set of offending coordinates
* Furthest offending column
* Health status and whether writes are allowed

Diagnostics are sanitized. They report value type, recognized controlled-value category, possible canonical field, and whether canonical row identity exists. They do not report descriptions, email bodies, private notes, or token-bearing URLs.

The audit has no repair, deletion, or compaction mode.

## Workflow gates

The following paths enforce Jobs integrity:

* Daily: pre-write validation through `src.workflow_validation`, and post-write enforcement before a successful daily completion record can be written
* Enrichment: explicit pre-write and post-write workflow steps
* Verification Health: read-only pre-calculation enforcement through `src.workflow_validation`
* Sheet UX Governance: pre-write and post-write workflow steps, plus direct validation of every Jobs-targeted batch request
* Workbook Capacity: read-only pre-run capture and post-run enforcement; compaction still preserves real out-of-bounds data and structural ranges

A failed gate stops the workflow and preserves the workbook for investigation.

## Direct API inventory

Run:

```powershell
python -m src.jobs_write_contract --audit --enforce
```

`config/jobs_write_allowlist.yml` records every reviewed direct Google Sheets write with its file, function, target, reason, canonical-data capability, and guard. Pull request tests fail when a new direct write is added without review and allowlisting.

## Incident response

When an out-of-bounds cell is detected:

1. Stop workflows that can write the workbook.
2. Preserve the failed workflow log and scanner JSON.
3. Record the coordinates, signal types, count, and furthest column.
4. Determine whether the value belongs to a canonical field and whether the canonical row has identity.
5. Do not compact, delete, overwrite, or widen the schema boundary.
6. Back up the workbook before any manual correction.
7. Correct the specific writer defect or displaced data with reviewed evidence.
8. Run the audit again and require a healthy result before resuming writes.

The historical fixture `Jobs!LTO680 = insufficient_evidence` is classified as a recognized controlled value and a possible `move_value_classification` displacement. The fixture must remain detectable and must never be overwritten or deleted automatically.

## Schema expansion approval

A legitimate expansion requires all of the following:

1. `JOB_FIELDS` intentionally gains append-only fields.
2. Existing headers remain an exact canonical prefix.
3. No out-of-bounds data or metadata exists.
4. Migration expands to the exact new canonical width.
5. New headers are written before records use them.
6. Full tests, regression readiness, and the gold-standard evaluation pass.
7. Post-merge production validation confirms the new exact width and zero out-of-bounds evidence.

Do not derive required width from malformed rows, the worksheet used range, or the highest populated column.
