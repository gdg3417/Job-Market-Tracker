# Sprint 24: LinkedIn lead-card recovery

## Goal

Recover valid LinkedIn lead postings when the digest collapses the title, company, and location into one linked label, and prevent alert headings from being interpreted as job records.

## Production defect

The June 21 Toyota digest was recorded as successfully processed, but LinkedIn job ID `4430066274` was associated with the alert heading instead of the lead card:

- Expected title: `National Manager, Product`
- Expected company: `Toyota North America`
- Expected location: `Plano, TX`
- Canonical URL: `https://www.linkedin.com/jobs/view/4430066274`

## Implementation

- Ignore LinkedIn alert metadata lines such as `Your job alert for...` and `New jobs in...` during card parsing.
- Reject alert metadata if it reaches field validation.
- Use the company-logo label as an identity anchor when a job-card label is collapsed into one line.
- Recover the title before the company label and the location after it.
- Preserve the existing multiline-card and HTML fallback behavior.
- Add a production-shaped Toyota regression that also passes the final data-quality gate.

## Post-merge production validation

1. Run `Job Tracker Daily Run` manually on `main` with `force_reprocess=true`.
2. Confirm Gmail message `19eeb7f9ad20df2f` has `attempt_count=2` and no failure.
3. Confirm Toyota job ID `4430066274` exists in `Jobs` and `Job_Sources` with the expected fields and canonical URL.
4. Confirm Toyota appears under `High-signal titles needing review` in the Digest.
5. Run the workflow again with `force_reprocess=false`.
6. Confirm `new_messages_processed=0`, `failed_messages=0`, and `backlog_remaining=0`.

The historical incorrect rejection can remain as audit history; the accepted canonical job record is the production acceptance criterion for this hotfix.
