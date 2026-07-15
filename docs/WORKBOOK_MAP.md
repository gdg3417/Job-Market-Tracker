# Workbook Map

## Ownership rules

`Jobs` is the canonical source of truth for job identity, scoring state, review decisions, applications, follow-up dates, compensation evidence, work-model evidence, and user notes.

Green headers identify fields that can be edited manually. Gray headers identify imported, calculated, generated, or system-managed fields.

Generated surfaces are read-only. Make corrections in `Jobs` or the applicable configuration worksheet, then refresh the presentation layer.

## Worksheet inventory

| Worksheet | Primary class | Edit policy | Purpose and overwrite behavior |
| --- | --- | --- | --- |
| `Jobs` | Canonical and user-editable | Edit only green columns | Canonical job identity, status, evidence, scoring, review, application, and follow-up record. System fields are rewritten by ingestion, enrichment, scoring, lifecycle, and refresh processes. |
| `Config_Searches` | Configuration and user-editable | User-managed | Search definitions, role families, keywords, locations, level, compensation, and active status. |
| `Config_Companies` | Configuration and user-editable | User-managed with controlled cleanup | Company sources, ATS configuration, aliases, career URLs, source quality, ingestion mode, and enrichment controls. Source-quality cleanup requires reviewed exact company IDs and matching URLs. |
| `Scoring_Rules` | Configuration and user-editable | User-managed | Active scoring rules and evidence signals. Changes should be regression tested. |
| `Target_Companies` | Configuration and user-editable | User-managed | Strategic target-company coverage and score boosts. |
| `Job_Sources` | Audit ledger and system-managed | Do not edit | Source lineage for canonical jobs. Preserves observed and authoritative source evidence. |
| `Rejected_Jobs` | Audit ledger and system-managed | Do not edit | Rejected alert records, reasons, and extraction diagnostics. |
| `Gmail_Messages` | Audit ledger and system-managed | Do not edit | Current Gmail message processing state, attempts, and accepted or rejected counts. |
| `Gmail_Failures` | Audit ledger and system-managed | Do not edit | Immutable message-level failure evidence, normalized category, stage, retry eligibility, systemic flag, and sanitized error. |
| `Enrichment_Queue` | System-managed | Do not edit | Deterministic enrichment work items, attempts, retry state, and errors. |
| `Enrichment_Evidence` | Audit ledger and system-managed | Do not edit | Extracted evidence, confidence, source URL, content hash, and acceptance state. |
| `Posting_Resolution` | System-managed with controlled manual fields | Use approved manual override fields only | Current authoritative-posting resolution state per job. Do not delete resolution history. |
| `Resolution_Candidates` | Audit ledger and system-managed | Do not edit | Candidate URLs and visible resolution score components. |
| `Source_Health` | System-managed | Do not edit | Current reliability state by company, platform, and source URL. |
| `Snapshots` | Audit ledger and system-managed | Do not edit | Historical job snapshots used for trend and audit analysis. |
| `Runs` | Audit ledger and system-managed | Do not edit | Workflow, validation, health, completion, and maintenance run history. |
| `Review_Queue` | Generated read-only surface | Do not edit | Current roles needing human review. Edit `Jobs`. Replaced on presentation refresh. |
| `Follow_Up_Queue` | Generated read-only surface | Do not edit | Current follow-up and application-aging work. Edit `Jobs`. Replaced on presentation refresh. |
| `Weekly_Value` | Generated read-only surface | Do not edit | Weekly tracker activity and value metrics. Replaced on presentation refresh. |
| `Weekly_Context` | Generated read-only surface | Do not edit | Weekly email contract and current action context. Replaced on presentation refresh. |
| `Surface_Status` | Generated read-only surface | Do not edit | Last refresh status, source run, rows written, warnings, and data as-of date for generated surfaces. |
| `Source_Audit` | Generated read-only surface | Do not edit | Latest live source classifications and retry policy evidence. Edit `Config_Companies` only through reviewed changes. |
| `Source_Yield` | Generated read-only surface | Do not edit | Four-week source and search performance with non-destructive recommendations. |
| `Dashboard` | Generated read-only surface | Do not edit | Executive summary, action queues, actionable verification health, portfolio coverage, and source health. |
| `Digest` | Generated read-only surface | Do not edit | Ranked job digest used by downstream presentation and email logic. |
| `Sheet_Guide` | Generated read-only surface | Do not edit | Workbook ownership, editability, color, filter, freeze, and dropdown guidance. Rewritten by governance. |

## Canonical schema boundary

`src/schema.py` validates the canonical schema for:

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
11. `Rejected_Jobs`
12. `Gmail_Messages`
13. `Enrichment_Queue`
14. `Enrichment_Evidence`
15. `Posting_Resolution`
16. `Resolution_Candidates`
17. `Source_Health`

Operational audit and presentation worksheets can be created outside the canonical schema without changing the `Jobs` field order.

## Where to make common changes

| Change | Worksheet | Field area |
| --- | --- | --- |
| Review a role | `Jobs` | Green review and interest fields |
| Dismiss a role | `Jobs` | Review status, interest decision, dismissal reason, and detail |
| Track an application or interview | `Jobs` | Application status, dates, URLs, next action, and follow-up fields |
| Correct compensation or work model | `Jobs` | Green manual evidence fields |
| Add notes | `Jobs` | Green notes fields |
| Change search coverage | `Config_Searches` | Search definition and active flag |
| Change a company source | `Config_Companies` | Source, ATS, enrichment, and active fields |
| Change strategic company coverage | `Target_Companies` | Target definition and active flag |
| Change scoring logic | `Scoring_Rules` | Rule definition and active flag |
| Correct a posting URL | `Posting_Resolution` or `Jobs` | Approved manual authoritative URL workflow |

## Safe refresh sequence

Run:

```powershell
python -m src.presentation_refresh --refresh --source-run "manual-maintenance" --governance
```

The refresh reads canonical `Jobs` once where practical, applies current exclusions and date normalization, writes generated surfaces idempotently, and records freshness in `Surface_Status`.

## Prohibited manual actions

1. Do not reorder or delete canonical `Jobs` columns.
2. Do not paste values into gray system-managed columns.
3. Do not edit generated surfaces as a substitute for changing `Jobs`.
4. Do not delete audit rows to hide errors or duplicates.
5. Do not add large unused row or column ranges.
6. Do not manually compact the workbook without a current capacity audit and backup.
