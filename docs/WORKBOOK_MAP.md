# Workbook Map

## Ownership rules

`Jobs` is the canonical source of truth for job identity, scoring state, review decisions, applications, follow-up dates, compensation evidence, work-model evidence, and user notes.

Green headers identify the normal user-editable fields on `Jobs` and the configuration worksheets. Gray headers identify imported, calculated, generated, or system-managed fields.

Generated surfaces are read-only. Make corrections in `Jobs` or the applicable configuration worksheet, then refresh the presentation layer.

`Posting_Resolution` has one narrow legacy exception to the color rule. Its durable manual override fields are intentionally entered on that system-managed worksheet, but the current governance policy does not color those fields green. Edit only the named manual fields described below.

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
| `Gmail_Failures` | Audit ledger and system-managed | Do not edit | Message-level failure evidence, normalized category, stage, retry eligibility, systemic flag, and sanitized error. Failure attempts receive durable identifiers; an existing identifier can be updated idempotently. |
| `Enrichment_Queue` | System-managed | Do not edit | Deterministic enrichment work items, attempts, retry state, and errors. |
| `Enrichment_Evidence` | Audit ledger and system-managed | Do not edit | Extracted evidence, confidence, source URL, content hash, and acceptance state. |
| `Posting_Resolution` | System-managed with a controlled manual exception | Edit only `manual_authoritative_url`, `manual_resolution_decision`, `manual_reviewer`, `manual_review_date`, and `manual_notes` | Current authoritative-posting resolution state per job. Resolver reruns preserve the manual fields until the decision is processed. Do not edit calculated resolution fields or delete prior evidence. |
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
| `Sheet_Guide` | Generated read-only surface | Do not edit | Workbook ownership, editability, color, filter, freeze, and dropdown guidance for worksheets included in the governance policy. Rewritten by governance. |

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

## Manual authoritative URL distinction

Two similarly named fields serve different purposes:

1. `Jobs.manual_authoritative_url` records a proposed URL or review cue. It remains manual intervention until the resolver validates the posting.
2. `Posting_Resolution.manual_authoritative_url`, together with a valid `manual_resolution_decision`, reviewer, and review date, is the durable resolver override input.

To accept or replace an authoritative posting:

1. Find the existing `Posting_Resolution` row for the exact `job_key`.
2. Enter the validated employer or ATS URL in `manual_authoritative_url`.
3. Set `manual_resolution_decision` to `accept` or `replace`.
4. Enter `manual_reviewer`, `manual_review_date`, and optional `manual_notes`.
5. Rerun authoritative resolution for the exact job key.

Use `remove` to remove a prior override, or `reject_automated` to block the current automated candidate. These decisions also require reviewer and review date. Do not manually change `resolution_state`, `authoritative_url`, confidence fields, or candidate rows.

## Where to make common changes

| Change | Worksheet | Field area |
| --- | --- | --- |
| Review a role | `Jobs` | Green review and interest fields |
| Dismiss a role | `Jobs` | Review status, interest decision, dismissal reason, and detail |
| Track an application or interview | `Jobs` | Application status, dates, URLs, next action, and follow-up fields |
| Correct compensation or work model | `Jobs` | Green manual evidence fields |
| Add notes | `Jobs` | Green notes fields |
| Record a proposed authoritative URL for review | `Jobs` | Green `manual_authoritative_url` field |
| Execute a validated authoritative-posting override | `Posting_Resolution` | The five named manual override fields only |
| Change search coverage | `Config_Searches` | Search definition and active flag |
| Change a company source | `Config_Companies` | Source, ATS, enrichment, and active fields |
| Change strategic company coverage | `Target_Companies` | Target definition and active flag |
| Change scoring logic | `Scoring_Rules` | Rule definition and active flag |

## Safe refresh sequence

Run:

```powershell
python -m src.presentation_refresh --refresh --source-run "manual-maintenance" --governance
```

The refresh reads canonical `Jobs` once where practical, applies current exclusions and date normalization, writes generated surfaces idempotently, and records freshness in `Surface_Status`.

## Prohibited manual actions

1. Do not reorder or delete canonical `Jobs` columns.
2. Do not paste values into gray system-managed columns, except for the five documented `Posting_Resolution` manual override fields.
3. Do not edit generated surfaces as a substitute for changing `Jobs`.
4. Do not delete audit rows to hide errors or duplicates.
5. Do not add large unused row or column ranges.
6. Do not manually compact the workbook without a current capacity audit and backup.
