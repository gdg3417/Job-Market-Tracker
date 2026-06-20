# Sprint 19: Scoring and Digest usefulness

## Goal

Make the Digest useful for passive job monitoring by raising strategic roles, suppressing low-value roles, and grouping weekly review rows into focused sections.

## Implemented changes

### Scoring rules

`config/scoring_rules.yml` now gives stronger positive signal coverage to:

```text
Commercial strategy
Revenue strategy
Business operations with executive cadence
Product line management
Category management
GM track and general management path
Chief of Staff to CEO, President, or GM
Pricing, margin, growth, and P&L ownership
```

The rules now suppress or exclude lower-value roles that should not create weekly review noise:

```text
Generic PMO
Generic project manager
Generic operations
Technician
Support specialist
Billing specialist
Insurance operations associate
Office manager
Project coordinator
Pure IT infrastructure
Job board landing or navigation terms
```

### Digest sections

`src/dashboard.py` now builds Digest rows in this review order:

```text
Immediate review
Strong fit
Target company watchlist
Needs salary research
Remote or short commute
P&L pathway
New this week
Closed or likely closed this week
Rejected source audit
```

Job rows are deduped across the main job sections so the same job does not appear repeatedly in the weekly review queue.

### Target company watchlist

The Digest can use `Target_Companies` and `Config_Companies` rows to surface watchlist company jobs even when the total score is below the strong-fit threshold. Active Tier 1, Tier 2, target, watchlist, high priority, or boosted rows qualify.

### Rejected source audit

The Digest now includes recent rejected rows that look source-related. This makes noisy static sources, job board navigation pages, alert metadata, and generic search-page rejections visible without reviewing the full `Rejected_Jobs` tab each week.

## Tests added

`tests/test_scoring.py` adds coverage for:

```text
Generic PMO downweighting
Support, billing, coordinator, technician, and IT support exclusions
Chief of Staff to GM strategic scoring
Existing watch, service operations, accounting, BI developer, and commute scoring behavior
```

`tests/test_dashboard.py` adds coverage for:

```text
Focused Sprint 19 Digest sections
Digest job deduplication
Target company watchlist inclusion
Rejected source audit row mapping
Dashboard metric labels
```

## Acceptance criteria mapping

| Criterion | Status |
| --- | --- |
| Digest has fewer but better rows | Implemented through focused sections and deduplication |
| Strong strategic roles are not buried | Implemented through boosted scoring and early Digest sections |
| Generic jobs stay in ignore | Implemented through hard exclusions and negative penalties |
| Review queue is usable weekly | Implemented through Immediate review, Strong fit, Target company watchlist, Needs salary research, Remote or short commute, and Rejected source audit sections |

## Validation commands

Run from PowerShell after pulling the branch:

```powershell
pytest
python -m src.schema --validate
python -m src.main --gmail-alerts-smoke-test
python -m src.main --static-pages-smoke-test
python -m src.dashboard
```
