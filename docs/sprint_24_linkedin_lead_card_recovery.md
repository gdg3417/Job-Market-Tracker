# Sprint 24: LinkedIn lead-card recovery

## Goal

Recover valid LinkedIn lead postings when digest markup collapses or omits a parseable direct job-card link, and prevent alert headings from being interpreted as job records.

## Production defect

The June 21 Toyota digest was recorded as successfully processed, but LinkedIn job ID `4430066274` was associated with the alert heading instead of the lead card:

- Expected title: `National Manager, Product`
- Expected company: `Toyota North America`
- Expected location: `Plano, TX`
- Canonical URL: `https://www.linkedin.com/jobs/view/4430066274`

## Implementation

- Ignore LinkedIn alert metadata lines such as `Your job alert for...` and `New jobs in...` during card parsing.
- Reject alert metadata if it reaches field validation.
- Normalize parseable Markdown and HTML links to their visible labels before line parsing.
- Bound each job-card text segment between the first direct link for that job and the first direct link for the next job.
- Recover title, company, and location from that bounded segment when the direct job-card link itself is malformed or absent.
- Continue using company-logo labels as identity anchors for collapsed one-line cards.
- Preserve existing multiline-card, HTML fallback, canonical URL, and deduplication behavior.
- Cover both the production-shaped malformed lead card and a valid neighboring card in regression tests.

## Post-merge production validation

1. Run `Job Tracker Daily Run` manually on `main` with `force_reprocess=true`.
2. Confirm Gmail message `19eeb7f9ad20df2f` has an increased `attempt_count` and no failure.
3. Confirm Toyota job ID `4430066274` exists in `Jobs` and `Job_Sources` with the expected fields and canonical URL.
4. Confirm Toyota appears under `High-signal titles needing review` in the Digest.
5. Run the workflow again with `force_reprocess=false`.
6. Confirm `new_messages_processed=0`, `failed_messages=0`, and `backlog_remaining=0`.

The historical incorrect rejection can remain as audit history; the accepted canonical job record is the production acceptance criterion for this hotfix.
