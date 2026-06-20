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


def test_digest_rows_include_focused_sprint_20_sections_without_duplicates():
    jobs = [
        make_job(job_key="immediate", total_score=90, alert_tier="immediate_review"),
        make_job(job_key="strong", title="Senior Manager, Revenue Strategy", total_score=80, alert_tier="strong_fit"),
        make_job(job_key="salary", title="Manager, Business Operations", salary_min=None, salary_max=None, total_comp_estimate=None, total_score=68, alert_tier="track_only"),
        make_job(job_key="commute", title="Manager, Pricing Strategy", remote_status="remote", work_model="remote", commute_estimate_minutes=None, total_score=66, alert_tier="track_only"),
        make_job(job_key="pnl", title="Manager, Product Line Strategy", location="Dallas, TX", remote_status="onsite", work_model="in_office", commute_estimate_minutes=45, total_score=64, alert_tier="ignore"),
    ]
    rows = build_digest_rows(jobs, as_of="2026-06-17")
    sections = [row[0] for row in rows]
    assert "Immediate review" in sections
    assert "Strong fit" in sections
    assert "Needs salary research" in sections
    assert "Remote or short commute" in sections
    assert "P&L pathway" in sections
    job_titles = [row[2] for row in rows if row[0] != "Rejected source audit"]
    assert len(job_titles) == len(set(job_titles))


def test_digest_caps_rows_per_section_at_sprint_20_limits():
    jobs = [make_job(job_key=f"immediate-{index}", title=f"Director Commercial Strategy {index}") for index in range(12)]
    rows = build_digest_rows(jobs, as_of="2026-06-17")
    assert sum(1 for row in rows if row[0] == "Immediate review") == 10


def test_target_company_watchlist_section_uses_target_company_rows():
    job = make_job(company="Fossil Group", title="Manager, Commercial Strategy", total_score=62, alert_tier="ignore", p_and_l_path_score=4)
    rows = build_digest_rows(
        [job],
        as_of="2026-06-17",
        target_company_rows=[{"company_name": "Fossil Group", "priority_tier": "Tier 1", "active": "TRUE"}],
    )
    assert any(row[0] == "Target company watchlist" for row in rows)


def test_rejected_source_audit_section_maps_rejected_rows():
    rows = build_digest_rows(
        [],
        rejected_job_rows=[
            {
                "source": "static_pages",
                "title": "Job Search Search Jobs",
                "company": "The Ladders",
                "url": "https://www.theladders.com/jobs/search-jobs",
                "rejection_reason": "source URL is a search page",
                "created_at": "2026-06-17T12:00:00Z",
            }
        ],
    )
    assert rows[0][0] == "Rejected source audit"
    assert rows[0][1] == "The Ladders"
    assert "search page" in rows[0][-1]


def test_digest_excludes_closed_old_jobs_from_open_sections():
    job = make_job(status="confirmed_closed", closed_date="2026-05-01", total_score=95)
    rows = build_digest_rows([job], as_of="2026-06-17")
    assert rows == []


def test_build_digest_values_includes_title_metadata_headers_and_rows():
    job = make_job()
    values = build_digest_values([job], as_of="2026-06-17")
    assert values[0] == ["Job Market Tracker Weekly Digest"]
    assert values[4] == DIGEST_HEADERS
    assert len(values) > 5


def test_dashboard_values_are_plain_executive_summary_not_formulas():
    job = make_job()
    digest_rows = build_digest_rows([job], as_of="2026-06-17")
    values = build_dashboard_values(
        [job],
        digest_rows=digest_rows,
        config_company_rows=[{"source_type": "static", "ingestion_mode": "static_direct", "active": "TRUE"}],
        rejected_job_rows=[],
        runs_rows=[{"run_type": "sprint_16_workflow_validation", "status": "success", "finished_at": "2026-06-17T12:00:00Z"}],
        generated_at="2026-06-17T13:00:00Z",
    )
    flattened = "\n".join(str(cell) for row in values for cell in row)
    for expected in [
        "This week's answer",
        "Review roles now",
        "Action queue",
        "Immediate review",
        "Strong fit",
        "Target company watchlist",
        "Needs salary research",
        "Remote or short commute",
        "P&L pathway",
        "Tracker health",
        "Source health",
        "Top roles to review",
        "Source cleanup queue",
    ]:
        assert expected in flattened
    assert "=COUNTIF" not in flattened
    assert "=COUNTIFS" not in flattened
    assert "=QUERY" not in flattened
    assert "#REF!" not in flattened
    assert "#VALUE!" not in flattened


def test_dashboard_says_no_roles_to_review_when_empty():
    values = build_dashboard_values([], digest_rows=[], rejected_job_rows=[])
    flattened = "\n".join(str(cell) for row in values for cell in row)
    assert "No action needed this week" in flattened
    assert "No roles to review" in flattened
