# Sprint 38 Post-Patch Rerun Checklist

Use this after pulling the readiness follow-up patch.

```powershell
cd "C:\Users\gdg34\OneDrive\Documents\GitHub\Job-Market-Tracker"
git fetch origin
git switch codex/sprint-38-lifecycle-readiness
git pull --ff-only origin codex/sprint-38-lifecycle-readiness

.\.venv\Scripts\Activate.ps1
pytest
python -m src.production_readiness --evaluate-regression --fixture data/regression/sprint38_gold_standard_jobs.json
python -m src.production_readiness --evaluate-readiness --dry-run
python -m src.production_readiness --evaluate-readiness --write-run
python -m src.dashboard --no-run-log
python -m src.schema --validate
```

Pass criteria:

1. Tests pass.
2. Regression pass rate is 1.0.
3. False-closure rate is 0.0.
4. `high_priority_sla_breaches` is 0 unless a high-priority job is aged, unresolved, and has no visible blocker.
5. `verification_conversion` may remain a warning.
6. Readiness is `ready` or `ready_with_warnings`.
7. Schema validation passes after Dashboard and Digest refresh.

Do not merge while readiness is `not_ready`.
