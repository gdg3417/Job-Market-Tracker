from src.dashboard import build_dashboard_values, build_digest_rows
from src.models import JobPosting


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX",
        "status": "open",
        "remote_status": "hybrid",
        "work_model": "hybrid",
        "required_office_days_per_week": 2,
        "commute_bucket": "15_to_30_minutes",
        "base_salary_min": 180000,
        "base_salary_max": 205000,
        "estimated_total_comp_min": 207000,
        "estimated_total_comp_max": 235750,
        "compensation_source_type": "employer_posted",
        "compensation_confidence": "confirmed",
        "total_score": 82,
        "verified_total_score": 82,
        "score_status": "verified",
        "alert_tier": "strong_fit",
        "potential_priority": "high",
        "potential_priority_score": 75,
        "role_family": "Commercial Strategy",
        "role_level": "Director",
        "p_and_l_path_score": 16,
        "description_text": "Own revenue growth and product line margin expansion.",
        "canonical_url": "https://example.com/job",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-27",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_dashboard_includes_move_value_sections_without_removing_existing_sections():
    jobs = [
        make_job(job_key="confirmed"),
        make_job(job_key="unknown-comp", base_salary_min=None, base_salary_max=None, estimated_total_comp_min=None, estimated_total_comp_max=None, compensation_source_type="unknown", work_model="unknown"),
    ]
    digest_rows = build_digest_rows(jobs, as_of="2026-06-27")
    values = build_dashboard_values(jobs, digest_rows=digest_rows, rejected_job_rows=[])
    flattened = "\n".join(str(cell) for row in values for cell in row)

    for expected in [
        "Action queue",
        "Tracker health",
        "Top roles to review",
        "Move-value intelligence",
        "Strong roles with confirmed compensation",
        "Strong roles with unknown compensation",
        "Roles requiring compensation follow-up",
        "Roles requiring work-model follow-up",
        "Source cleanup queue",
    ]:
        assert expected in flattened

    assert "#REF!" not in flattened
    assert "#VALUE!" not in flattened
