from src.dashboard import DIGEST_HEADERS, build_dashboard_values, build_digest_rows, build_digest_values
from src.models import JobPosting


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX Hybrid",
        "remote_status": "hybrid",
        "work_model": "hybrid",
        "commute_estimate_minutes": 25,
        "salary_min": 170000,
        "salary_max": 220000,
        "total_comp_estimate": 230000,
        "canonical_url": "https://example.com/job",
        "first_seen_date": "2026-06-15",
        "last_seen_date": "2026-06-17",
        "status": "open",
        "role_family": "Commercial Strategy",
        "role_level": "Director",
        "p_and_l_path_score": 18,
        "growth_ownership_score": 18,
        "executive_exposure_score": 14,
        "operating_cadence_score": 9,
        "total_score": 90,
        "alert_tier": "immediate_review",
        "description_text": "Own revenue growth, margin expansion, and P&L pathway for a business unit.",
        "score_explanation": "total=90",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_digest_headers_include_review_fields():
    assert DIGEST_HEADERS[:4] == ["digest_section", "company", "title", "location"]
    assert "total_score" in DIGEST_HEADERS
    assert "canonical_url" in DIGEST_HEADERS
    assert "score_explanation" in DIGEST_HEADERS


def test_digest_rows_include_immediate_review_pnl_and_commute_sections():
    job = make_job()
    rows = build_digest_rows([job], as_of="2026-06-17")
    sections = [row[0] for row in rows]
    assert "Immediate review" in sections
    assert "P&L pathway" in sections
    assert "Remote, hybrid, or short commute" in sections
    assert "New this week" in sections


def test_digest_excludes_closed_old_jobs_from_open_sections():
    job = make_job(status="confirmed_closed", closed_date="2026-05-01", total_score=95)
    rows = build_digest_rows([job], as_of="2026-06-17")
    assert rows == []


def test_missing_salary_review_section_for_scored_open_job():
    job = make_job(salary_min=None, salary_max=None, total_comp_estimate=None, total_score=78, alert_tier="strong_fit")
    rows = build_digest_rows([job], as_of="2026-06-17")
    sections = [row[0] for row in rows]
    assert "Missing salary review" in sections


def test_build_digest_values_includes_title_metadata_headers_and_rows():
    job = make_job()
    values = build_digest_values([job], as_of="2026-06-17")
    assert values[0] == ["Job Market Tracker Weekly Digest"]
    assert values[4] == DIGEST_HEADERS
    assert len(values) > 5


def test_dashboard_values_include_core_sprint_11_sections():
    values = build_dashboard_values()
    flattened = "\n".join(str(cell) for row in values for cell in row)
    for expected in [
        "New jobs this week",
        "Immediate review jobs",
        "Strong fit open jobs",
        "P&L pathway jobs",
        "Remote jobs",
        "Jobs within 15 minutes",
        "Jobs within 30 minutes",
        "Salary range by role family",
        "Average days open by role family",
        "Companies with repeat postings",
        "Jobs with missing salary",
    ]:
        assert expected in flattened
