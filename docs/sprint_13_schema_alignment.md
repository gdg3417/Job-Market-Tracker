# Sprint 13: Workbook schema alignment and validation

## Purpose

Sprint 13 locks the code and Google Sheets workbook to the same tab headers before additional ingestion changes are made.

The primary risks addressed are silent field drops during writes, stale `Runs` headers, stale dashboard and digest layouts, and workbook timezone drift.

## Canonical schema

Canonical headers now live in `src/schema.py` for these tabs:

1. `Jobs`
2. `Job_Sources`
3. `Runs`
4. `Dashboard`
5. `Digest`
6. `Snapshots`
7. `Config_Searches`
8. `Config_Companies`
9. `Scoring_Rules`
10. `Target_Companies`

`Digest` is validated against its Sprint 11 generated header row on row 5. `Dashboard` is validated against its Sprint 11 title row.

## Commands

Validate the live workbook:

```powershell
python -m src.schema --validate
```

Repair canonical header rows and set the workbook timezone to Central:

```powershell
python -m src.schema --repair-headers
```

Run the standard local checks:

```powershell
pytest
python -m src.schema --validate
python -m src.main --sheets-smoke-test
```

## Write safety

`SheetClient` now validates headers before writes. Writes fail when:

1. A known workbook tab is missing a canonical header required by the code.
2. A record contains a key that is not present in the worksheet header row.

This prevents run records, job records, and source records from silently losing fields when the workbook schema is stale.

## Current workbook alignment

The live workbook should use timezone `America/Chicago`.

The `Runs` tab should support this richer run record shape:

```text
run_id
run_type
source_type
source_name
status
started_at
finished_at
duration_seconds
records_found
records_inserted
records_updated
records_failed
rows_read
config_companies_rows
config_searches_rows
companies_read
searches_read
error_message
notes
created_at
updated_at
```

## Boundaries

Sprint 13 does not add new sourcing features and does not harden Gmail parsing. Gmail alert cleanup is Sprint 14.
