# Sprint 29: External Search Fallback and Match Validation

Sprint 29 adds a zero-cost external search stage after direct-link enrichment and configured company or ATS discovery cannot establish one safe posting match.

## Processing order

The combined enrichment pipeline now runs in this order:

1. Process the stored lead URL.
2. Query the configured company career site or supported ATS source.
3. Route unresolved high-potential jobs to external search.
4. Search the configured career domain first when one exists.
5. Search the broader web when the official-domain query is insufficient.
6. Retain only configured company domains, supported ATS domains, or conservative company-domain candidates.
7. Fetch each retained page and require it to parse as a specific job posting.
8. Apply the existing title, company, location, seniority, role-family, and posting-ID matcher.
9. Merge only one uniquely plausible candidate with a confidence score of at least 80.
10. Mark scores from 60 through 79, or multiple plausible candidates, as ambiguous.
11. Reject candidates below 60 and preserve a manual review URL.

## Zero-cost search provider

The default provider uses DuckDuckGo's HTML search endpoint and requires no API key. Search is bounded by three controls:

```text
query_budget
results_per_query
candidate_page_budget
```

Default values are three queries, five retained results per query, and five fetched candidate pages per job.

Use `--no-web-search` to disable automated search while still generating manual review links.

## Discovery evidence is not scoring evidence

Search result titles and snippets are discovery aids only. They are never accepted or merged as job evidence.

A candidate must pass all of these gates before it can modify a job:

1. Safe public HTTP or HTTPS URL.
2. Not a denied search engine or general job-board domain.
3. Configured company domain, supported ATS domain, or conservative company-domain match.
4. Final redirect remains authoritative.
5. Declared canonical URL remains authoritative.
6. Full page parses as a specific job posting.
7. Existing match confidence reaches the automatic threshold.
8. No second plausible candidate remains.

## Supported authoritative ATS domains

External search can automatically validate postings hosted by these established ATS families:

```text
Greenhouse
Lever
Ashby
SmartRecruiters
Workday
I-CIMS
SuccessFactors
Phenom
Oracle Cloud Recruiting
```

General job boards such as LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, CareerBuilder, SimplyHired, and The Ladders remain manual discovery sources. They are not automatically accepted by this stage.

## Search cache

External search query results are stored as rejected discovery rows in `Enrichment_Evidence` with source type `external_search_discovery`.

The cache is keyed by provider and normalized query text. A result remains fresh for 24 hours. Cache hits do not consume the live query budget, so a later run can use its remaining budget on the next uncached query instead of repeating prior work.

## Manual review links

Unresolved jobs receive a preferred manual review URL in two existing fields:

```text
enrichment_source_url
score_explanation as manual_review_url=<url>
```

This makes the review URL visible in the existing Digest and Dashboard output without replacing the original lead URL or changing job identity.

The link order is:

1. Configured company career search.
2. Google.
3. Bing.
4. DuckDuckGo.
5. LinkedIn.
6. Indeed.

## Commands

Preview all three enrichment stages:

```powershell
python -m src.enrichment.pipeline --dry-run
```

Run direct-link, company or ATS, and external-search enrichment:

```powershell
python -m src.enrichment.pipeline --run --direct-limit 10 --company-limit 10 --external-limit 10
```

Run only external search:

```powershell
python -m src.enrichment.search_run --run --limit 10
```

Generate manual review links without automated web search:

```powershell
python -m src.enrichment.search_run --run --limit 10 --no-web-search
```

Validate a manually discovered authoritative candidate for one existing job:

```powershell
python -m src.enrichment.search_run --run --job-key "<job_key>" --candidate-url "<candidate_url>"
```

The manual candidate command can replay a terminal external-search queue item. It still applies URL authority, full-page parsing, match confidence, and unique-candidate validation.

Before the first workbook-backed run:

```powershell
python -m src.schema --migrate
python -m src.schema --validate
```

## Sprint boundaries

Sprint 29 does not complete verified scoring. It only collects and validates authoritative evidence.

The remaining roadmap stays separated:

1. Sprint 30: company context enrichment and completed verified scoring.
2. Sprint 31: lifecycle management, retries, expiry, closure, and manual resolution.
3. Sprint 32: controlled backfill, scheduled production rollout, monitoring, and operational documentation.
