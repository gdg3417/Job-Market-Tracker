# Sprint 27: Enrichment Queue and Direct-Link Extraction

## Purpose

Sprint 27 creates the enrichment infrastructure used to recover authoritative job details from URLs already stored in `Jobs`. It does not perform broad web search, company career-site discovery, or ATS board search. Those stages remain in later sprints.

The direct-link stage never creates a new job. It updates an existing `Jobs` row only after the fetched posting passes the automatic match threshold.

## Workbook tabs

### Enrichment_Queue

One deterministic queue row is created for each eligible combination of `job_key`, lead URL, and direct-link stage. Re-running the enqueue step with the same inputs does not create another row.

Important fields:

* `status`: Current direct-link result.
* `attempt_count`: Number of attempted fetches.
* `matched_url`: Final or canonical URL evaluated.
* `match_confidence`: Confidence from 0 through 100.
* `fields_recovered`: Evidence fields found on the posting.
* `error_type` and `error_message`: Structured failure details.

### Enrichment_Evidence

This tab is the audit trail for fetched evidence and failures. It stores parsed fields and a SHA-256 content hash, not the full HTML page.

Evidence is accepted only when title and company matching reaches the automatic threshold. Ambiguous or rejected evidence remains visible but is not merged into `Jobs`.

## Eligibility

A job enters direct-link enrichment when all of the following are true:

1. The job is open or reopened.
2. Its score status is provisional or partially verified.
3. Its potential priority is high or medium.
4. Its enrichment status is pending or retryable failure.
5. It has a job key, credible title, credible company, and HTTP or HTTPS URL.

Sprint 26 normally sets `pending` only for configured high-potential jobs, so Sprint 27 consumes that decision rather than recalculating queue eligibility independently.

## Fetch controls

The fetcher applies:

* A 15 second request timeout.
* A maximum of five redirects.
* A 2 MB response limit.
* HTML and JSON-LD content-type validation.
* Per-domain rate limiting.
* Three direct-link attempts before a retryable failure becomes permanent.
* Exponential retry scheduling.
* No browser automation.
* No direct dependency on LinkedIn or Indeed scraping.

## Extraction order

For each fetched page:

1. Parse JSON-LD `JobPosting` data.
2. Fall back to embedded metadata and visible structured job content.
3. Recover LinkedIn company, title, and location from metadata titles formatted as `Company hiring Title in Location | LinkedIn` when structured employer metadata is absent.
4. Reject generic career pages and other non-job pages.
5. Capture the canonical URL and parsed evidence.
6. Compare the source title, company, location, seniority, role family, and posting ID with the existing job.
7. Merge only when match confidence is at least 80.

Provider-prefixed source IDs such as `linkedin-4417965465` are matched against the underlying posting ID in canonical URLs.

## Safe merge behavior

The direct source may add or improve:

* Description
* Location
* Salary range and currency
* Remote status and work model
* Canonical employer or ATS URL
* Evidence completeness
* Enrichment timestamps and match confidence

The merge does not change `job_key`, source identity, title, or company. A shorter description does not replace a stronger existing description. Existing salary values are not overwritten automatically. The original Gmail or static lead remains in `Job_Sources`.

A successful direct-link enrichment is marked `enriched` or `partial`. It may move the score state to `partially_verified`, but Sprint 27 does not create a final verified fit score. Verified rescoring remains part of Sprint 30.

## Commands

Preview eligible jobs without writes:

```powershell
python -m src.enrichment.run --dry-run
```

Create the new worksheets, enqueue eligible jobs, and process up to ten direct URLs:

```powershell
python -m src.enrichment.run --run --limit 10
```

Process one existing job:

```powershell
python -m src.enrichment.run --run --job-key "<job_key>" --limit 1
```

Replay one existing terminal queue item after a parser or matcher correction:

```powershell
python -m src.enrichment.run --run --job-key "<job_key>" --replay --limit 1
```

Replay requires an exact job key and an existing queue row whose status is `not_found`, `ambiguous`, or `permanent_failure`. It reuses the deterministic queue ID. When the fetched content hash is unchanged, it updates the existing evidence row rather than appending a duplicate.

Validate the workbook after the run:

```powershell
python -m src.schema --validate
```

## Topgolf and Toyota regression check

After Sprint 26 has placed both jobs in pending enrichment, run the direct-link command and verify:

1. Each job has exactly one deterministic queue row for its current lead URL.
2. Each attempted fetch has an evidence or failure record.
3. No duplicate `Jobs` row is created.
4. Mismatched evidence is not merged.
5. A confident employer or ATS match updates the existing job and records its source URL and confidence.

If the stored lead URL is a blocked LinkedIn page or another non-authoritative page, the queue should record the direct-stage result without inventing details. Company and ATS discovery is added in Sprint 28.
