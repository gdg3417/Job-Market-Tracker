from __future__ import annotations

from src.models import JobPosting


def build_digest_rows(jobs: list[JobPosting], minimum_score: int = 65) -> list[dict[str, str | int | None]]:
    selected = [job for job in jobs if job.total_score >= minimum_score and job.status in {"open", "reopened"}]
    selected.sort(key=lambda job: job.total_score, reverse=True)
    return [
        {
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "role_family": job.role_family,
            "role_level": job.role_level,
            "total_score": job.total_score,
            "alert_tier": job.alert_tier,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "canonical_url": job.canonical_url,
            "score_explanation": job.score_explanation,
        }
        for job in selected
    ]
