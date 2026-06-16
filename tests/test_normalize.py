from src.normalize import (
    build_job_key,
    clean_text,
    infer_work_model,
    normalize_raw_job,
    normalize_url,
    parse_salary,
)


def test_normalize_raw_job_handles_missing_salary_and_location():
    job = normalize_raw_job({"company": "Acme", "title": "Manager, Commercial Strategy"})
    assert job.company == "Acme"
    assert job.location == ""
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.role_level == "Manager"
    assert job.role_family == "Commercial Strategy"
    assert job.status == "open"
    assert job.alert_tier == "unscored"


def test_normalize_url_removes_tracking_params_and_normalizes_host():
    url = normalize_url("https://Example.com/jobs/123/?utm_source=linkedin&foo=bar")
    assert url == "https://example.com/jobs/123?foo=bar"


def test_parse_salary_range():
    assert parse_salary("$150k - $210k") == (150000, 210000)
    assert parse_salary("USD 140,000 to 175,000 per year") == (140000, 175000)


def test_parse_salary_missing_or_non_salary_text():
    assert parse_salary("") == (None, None)
    assert parse_salary("Competitive salary") == (None, None)


def test_build_job_key_is_stable():
    first = build_job_key("Acme", "Senior Manager Strategy", "Plano TX")
    second = build_job_key("Acme", "Senior Manager Strategy", "Plano TX")
    assert first == second


def test_clean_text_strips_html_and_entities():
    assert clean_text("<p>Revenue &amp; Margin</p>") == "Revenue & Margin"


def test_infer_work_model_avoids_false_remote_positive():
    assert infer_work_model("Strategy Manager", "Dallas, TX", "This role is not remote.") == ("onsite", "in_office")
    assert infer_work_model("Strategy Manager", "Remote, US", "") == ("remote", "remote")


def test_normalize_raw_job_maps_source_fields_and_dates():
    job = normalize_raw_job(
        {
            "company_name": "Example Co",
            "job_title": "Senior Manager, Business Operations",
            "locations": "Remote - United States",
            "absolute_url": "https://example.com/job/1?gh_src=abc&dept=strategy",
            "id": "abc123",
            "compensation": "CAD 180k - 220k",
            "description": "Own operating cadence with executive leadership.",
        },
        source_primary="greenhouse",
        seen_date="2026-06-16",
    )
    assert job.company == "Example Co"
    assert job.location == "Remote, US"
    assert job.source_primary == "greenhouse"
    assert job.source_job_id == "abc123"
    assert job.salary_min == 180000
    assert job.salary_max == 220000
    assert job.currency == "CAD"
    assert job.first_seen_date == "2026-06-16"
    assert job.last_seen_date == "2026-06-16"
    assert job.role_family == "Business Operations"
