# Sprint 18: Source configuration cleanup

## Goal

Improve source quality by disabling or correcting low-value sources before they create noisy static ingestion results.

## What changed

Sprint 18 adds explicit source governance to `Config_Companies`.

New `Config_Companies` fields:

```text
source_quality
ingestion_mode
```

Supported `source_quality` values:

```text
success
empty
failed
too_noisy
needs_manual_url_correction
disable_recommended
```

Supported `ingestion_mode` values:

```text
gmail_only
static_direct
ats_greenhouse
ats_lever
manual_review_only
disabled
```

## Source audit command

Run the audit without changing the workbook:

```powershell
python -m src.source_audit
```

Apply recommendations to `Config_Companies`:

```powershell
python -m src.source_audit --apply-recommendations
```

The apply mode updates `source_quality`, `ingestion_mode`, and audit notes. It also sets `active` to `FALSE` for rows that are recommended as `disabled` or `manual_review_only` so they do not keep running as unattended static sources.

## Workbook schema repair

Sprint 18 changes the canonical `Config_Companies` header row. Run this once before running the audit in apply mode:

```powershell
python -m src.schema --repair-headers
python -m src.schema --validate
```

## Source rules

Job boards should not run as generic static sources.

Recommended treatment:

| Source type | Recommended ingestion mode | Reason |
| --- | --- | --- |
| LinkedIn | `gmail_only` | Static company or search pages are noisy. Direct postings are better through Gmail alerts. |
| Indeed | `gmail_only` | Search and category pages are not stable direct posting sources. |
| Google Jobs | `gmail_only` | It is a search surface, not a company career page source. |
| Built In | `gmail_only` | It can produce generic links unless constrained to strong direct postings. |
| The Ladders | `disabled` | Search pages are too noisy for static ingestion. |
| Greenhouse | `ats_greenhouse` | Use the structured Greenhouse path. |
| Lever | `ats_lever` | Use the structured Lever path. |
| Company career page | `static_direct` | Acceptable when it is a reliable company-owned career page. |
| Broken or JavaScript-heavy source | `manual_review_only` | Do not run unattended until corrected. |

## Known source cleanup flags

The audit explicitly flags these known bad source rows from Sprint 18 planning:

| Company | Audit status | Recommended ingestion mode | Reason |
| --- | --- | --- | --- |
| Fossil Group | `failed` | `manual_review_only` | Static source returned 403. |
| Lennox | `needs_manual_url_correction` | `manual_review_only` | Source URL had a DNS failure. |
| Toyota Financial Services | `needs_manual_url_correction` | `manual_review_only` | Source URL returned 404. |
| Mary Kay | `needs_manual_url_correction` | `manual_review_only` | Source URL returned 404. |

The audit does not hard-code replacement URLs for these rows because a stale or guessed career URL would reintroduce the same problem. Correct the URL manually only after confirming the official active careers page or ATS source.

## Runs output

Each source audit appends a `Runs` record with:

```text
run_type = sprint_18_source_configuration_audit
source_type = config_companies
source_name = Config_Companies source audit
```

The `notes` field includes JSON counts by audit status and recommended ingestion mode.

## Acceptance criteria coverage

| Acceptance criterion | Coverage |
| --- | --- |
| Job boards are not generating generic static-page jobs | Source audit recommends `gmail_only` or `disabled`; apply mode prevents known bad static rows from remaining unattended. |
| Failed source URLs are corrected or disabled | Failed rows are marked `manual_review_only` or `disabled`; apply mode sets inactive for those rows. |
| Static page ingestion focuses on reliable direct career pages | `static_direct` is reserved for company-owned career or job pages and ATS-style sources. |
| Runs failure counts become useful instead of noisy | Source audit records issue counts in `Runs`, separate from ingestion failures. |

## Validation commands

```powershell
pytest
python -m src.schema --repair-headers
python -m src.schema --validate
python -m src.source_audit
python -m src.source_audit --apply-recommendations
python -m src.main --static-pages-smoke-test
python -m src.dashboard
```
