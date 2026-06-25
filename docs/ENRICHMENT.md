# Enrichment

## Entry criteria

A job enters enrichment when it is open or reopened, not hard-excluded, sparse enough to require evidence, and high or medium potential according to the configured priority rules.

Potential priority is not a final fit score. Missing salary or description reduces evidence completeness, not role quality.

## Stage order

1. Direct lead URL
2. Configured company career site or ATS
3. External search discovery
4. Authoritative candidate fetch
5. Match validation
6. Evidence merge
7. Verified scoring
8. Lifecycle monitoring

Search-result titles and snippets are discovery data only. They never award verified score points.

## Authority and merge rules

An automatic merge requires match confidence of at least 80 and a fetched employer or supported ATS posting.

New evidence may replace an existing value only when it is non-empty and more authoritative or more complete. Original Gmail message IDs, posting IDs, lead URLs, `Job_Sources`, and evidence provenance remain intact.

## Production commands

```powershell
python -m src.enrichment.production --dry-run --mode daily
python -m src.enrichment.production --run --mode daily
python -m src.enrichment.production --run --mode weekly
python -m src.enrichment.production --run --mode backfill
```

Use an exact job key for a controlled run:

```powershell
python -m src.enrichment.production --run --mode backfill --job-key "<job_key>"
```

## Ambiguous matches

An ambiguous match remains visible on Dashboard and in the queue. Review the candidate URL, title, company, location, and confidence before changing the job.

Do not copy search snippets into Jobs. Supply the authoritative posting URL in `Jobs.canonical_url`, keep the original lead in `Job_Sources`, then run the exact job through the production runner.

## Force re-enrichment

Use the existing replay option only for one known terminal direct-link queue item after a parser, matcher, or URL correction:

```powershell
python -m src.enrichment.pipeline --run --job-key "<job_key>" --replay
```

The replay command intentionally requires an exact `job_key`.

## Rejecting an incorrect match

Do not delete evidence. Set the queue item to `ambiguous` or `not_found`, retain the evidence row with `accepted=false`, and record the reason. Correct the canonical URL only when an authoritative posting has been manually verified.

## Adding an ATS connector

1. Add or update the company in `Config_Companies`.
2. Set the canonical company name and aliases.
3. Set the ATS platform and public board identifier.
4. Keep enrichment inactive until fixture tests pass.
5. Add exact, near-match, wrong-company, wrong-location, and ambiguity tests.
6. Enable the source and run one exact `job_key`.
