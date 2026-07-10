# Sprint 45 Hotfix: Dismissed Weekly Context Matches

## Problem

Production validation of `Weekly_Context` found that roles with `review_status = dismissed` could still appear under `New Strong Matches`.

The completed-week `Follow-ups Due` metric could also differ from the current follow-up action list without explaining that the two sections use different date scopes.

## Fix

1. The production refresh now pre-filters dismissed roles before building weekly review and new-match recommendations.
2. Filtering happens before the top-match limit is applied, so the next eligible open role backfills the list.
3. The summary period now states both scopes:
   - weekly metrics use the latest completed Monday through Sunday period;
   - action items are current as of the refresh date.
4. The canonical Sprint 45 generator remains unchanged. The workflow invokes `src.weekly_context_hotfix` as an isolated production hotfix.

## Validation

The focused weekly workflow test glob includes `tests/test_weekly_context_hotfix.py`.

Regression coverage confirms:

1. A dismissed high-scoring match is excluded.
2. The next eligible open match fills the configured match limit.
3. The summary text distinguishes completed-week metrics from current action items.

## Post-merge check

Run `Job Tracker Weekly Value Refresh` manually on `main`, then confirm:

1. `Ethos - Director of Sales Strategy and Operations` is absent from `New Strong Matches` while dismissed.
2. `Mouawad CS - Corporate Strategy Director` is absent from `New Strong Matches` while dismissed.
3. The Summary Week value includes `(weekly metrics)` and `current action items as of`.
