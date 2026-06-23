# Sprint 25: Plain-text LinkedIn card ordering

## Goal

Correct LinkedIn plain-text digest parsing when title, company, and location appear before a label-free View job URL.

## Root cause

Sprint 24 read the text after a plain-text job URL before the text preceding it. In the Toyota email, the text after Toyota's URL belonged to the next Flooret card. This paired LinkedIn job ID 4430066274 with the wrong title and company.

## Implementation

- For labeled Markdown or HTML job links, retain label and following-segment parsing before preceding context.
- For label-free plain-text job links, parse the preceding context before the following segment.
- Preserve the segment fallback for layouts where a company-logo link precedes card text.
- Add a production-shaped regression using the first two plain-text blocks from the Toyota email.
- Assert that 4430066274 maps to National Manager, Product at Toyota North America in Plano, TX.
- Assert that 4430017649 maps to Head of Product at Flooret in Grapevine, TX.
- Confirm the Toyota record passes the final data-quality gate.

## Post-merge validation

1. Run Job Tracker Daily Run from main with force_reprocess enabled.
2. Confirm Toyota job ID 4430066274 exists in Jobs and Job_Sources with the correct fields.
3. Confirm Toyota appears in the Digest review section.
4. Run the workflow again with force_reprocess disabled.
5. Confirm zero newly processed messages, zero failures, and zero backlog.
