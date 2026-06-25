# Sprint 28: Company Career Site and ATS Discovery

Sprint 28 adds an authoritative company and applicant tracking system discovery stage after direct-link enrichment fails or produces an ambiguous result.

## Processing order

The combined enrichment pipeline runs in this order:

1. Process the stored lead URL.
2. Preserve any accepted direct-link evidence.
3. Route unresolved high-potential jobs to the configured company and ATS stage.
4. Resolve the employer through exact canonical names and explicit aliases.
5. Query a supported public ATS endpoint or retain the configured official career search path.
6. Rank candidates with the existing title, company, location, seniority, role-family, and posting-ID matcher.
7. Merge only one candidate that reaches the automatic threshold.
8. Mark multiple plausible candidates as ambiguous.
9. Leave unresolved jobs available for the Sprint 29 external-search stage.

## Config_Companies additions

Sprint 28 appends these fields without moving existing columns:

```text
canonical_company_name
company_aliases
career_domain
career_search_url
ats_company_id
ats_board_token
enrichment_mode
enrichment_active
enrichment_notes
```

Aliases use `|`, `;`, or line breaks as separators. Alias matching is exact after normalization. It identifies candidate company configurations but does not bypass posting-level match validation.

## Supported ATS adapters

Automated public adapters:

```text
Greenhouse
Lever
Ashby
SmartRecruiters
```

Configured official source paths without generic landing-page scraping:

```text
Workday
iCIMS
SuccessFactors
Phenom
Oracle recruiting platforms
Company-specific career APIs without an implemented adapter
```

Dynamic platforms return `configured_only` until a stable endpoint is supplied. Their career search URL is recorded for later review and Sprint 29 fallback.

## Default regression company paths

The code includes fallback source configurations for Topgolf and Toyota North America. Workbook rows can supplement or override these defaults without erasing unspecified default fields.

The aliases are discovery aids only. Toyota Financial Services and other related entities still require the posting itself to pass company, title, and location validation.

## Commands

Preview both enrichment stages:

```powershell
python -m src.enrichment.pipeline --dry-run
```

Run direct-link enrichment followed by company and ATS discovery:

```powershell
python -m src.enrichment.pipeline --run --direct-limit 10 --company-limit 10
```

Run only the company and ATS stage:

```powershell
python -m src.enrichment.company_run --run --limit 10
```

Restrict processing to one existing job:

```powershell
python -m src.enrichment.pipeline --run --job-key "<job_key>" --direct-limit 1 --company-limit 1
```

Before the first workbook-backed Sprint 28 run:

```powershell
python -m src.schema --migrate
python -m src.schema --validate
```

## Safety rules

1. Company resolution does not use fuzzy alias matching.
2. Generic career landing pages are not treated as postings.
3. Search paths and unsupported dynamic ATS pages are discovery references, not scoring evidence.
4. Candidate descriptions are stored as structured evidence without raw HTML.
5. Wrong-company candidates are rejected.
6. Multiple plausible candidates require review.
7. Company-stage repetition does not create duplicate jobs or evidence.
8. One adapter failure does not stop other queued jobs.
