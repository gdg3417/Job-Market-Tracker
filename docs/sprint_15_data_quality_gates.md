# Sprint 15 data quality gates

## Goal

Stop polluted records before the daily workflow is allowed to run unattended.

Sprint 15 adds a final validation layer before candidate records are written to `Jobs` and `Job_Sources`. Rejected records are captured in `Rejected_Jobs` with explicit rejection reasons.

## Implemented behavior

Final data quality gates reject:

1. Generic alert or search titles such as `Job Search`, `Jobs Near Me`, `New jobs match your preferences`, and `Your job alert for...`.
2. Static page records where the URL is a search page, category page, near-me page, landing page, or generic job board navigation page.
3. Gmail records where title or company text looks like alert metadata.
4. Tracking and image asset URLs such as LinkedIn static asset hosts.
5. Manual review rows unless they come from a trusted company career page and the title passes stronger role checks.
6. Job board navigation URLs that do not represent direct job postings.

Accepted direct posting examples include:

```text
https://www.linkedin.com/jobs/view/4242424242/
https://careers.acme.example/jobs/director-commercial-strategy-12345
https://jobs.lever.co/acme/8f9a7b6c5d4e3f2a1b
```

Rejected source patterns include:

```text
The Ladders search URLs
LinkedIn search URLs
LinkedIn logo or tracking asset URLs
near-me job pages
category browse pages
resume pages
profile pages
help pages
services pages
```

## Main files

```text
src/data_quality.py
src/job_upsert.py
src/main.py
src/schema.py
tests/test_data_quality.py
tests/test_job_upsert.py
```

## Workbook impact

Sprint 15 adds this tab to the canonical workbook schema:

```text
Rejected_Jobs
```

Expected headers:

```text
rejected_id
source
message_id
thread_id
subject
sender
received_date
title
company
location
url
confidence
rejection_reason
extraction_notes
raw_evidence
created_at
updated_at
```

`Rejected_Jobs` should be used for source cleanup and parser hardening. It should not be treated as a manual import queue for `Jobs`.

## Validation examples covered by tests

The tests cover bad live examples including:

```text
Job Search Search Jobs...
Jobs Near Me Jobs in my city
New jobs match your preferences.
Your job alert for project manager in Dallas
The Ladders search URL
LinkedIn company logo tracking URL
```

They also verify valid direct posting examples, including LinkedIn `/jobs/view/` and direct company career posting URLs.

## Operational checks

Run:

```powershell
pytest
python -m src.schema --validate
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
```

Expected result:

1. `pytest` passes.
2. Schema validation includes `Rejected_Jobs` and returns `ok: true`.
3. Gmail smoke test does not recreate known alert metadata rows in `Jobs`.
4. Static pages smoke test does not recreate generic The Ladders search rows in `Jobs`.
5. Rejected candidate rows are written to `Rejected_Jobs` with reasons.

## Cleanup guidance

When cleaning the live Sheet:

1. Move known bad `Jobs` rows to `Rejected_Jobs` if historical evidence is useful.
2. Remove corresponding `Job_Sources` rows for bad jobs.
3. Archive or clearly mark malformed old `Runs` rows from before schema repair.
4. Do not loosen the quality gate without adding a specific regression test first.

## Source quality rule

Static page ingestion should not be used as a generic job board scraper. It should prioritize target company career pages and direct postings. Job boards should generally enter through Gmail alerts, explicit APIs, ATS integrations, or be disabled until a safer source path exists.
