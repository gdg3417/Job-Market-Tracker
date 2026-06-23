from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.company_context import company_context_for_name
from src.models import JobPosting
from src.scoring import score_job


def score_gmail_jobs_with_company_context(
    jobs: Iterable[JobPosting],
    scoring_rules: dict[str, Any],
    company_contexts: dict[str, dict[str, Any]] | None,
) -> list[JobPosting]:
    return [
        score_job(
            job,
            scoring_rules,
            company_context=company_context_for_name(job.company, company_contexts),
        )
        for job in jobs
    ]
