from src.normalize import build_job_key, normalize_raw_job, normalize_url, parse_salary


def test_normalize_raw_job_handles_missing_salary_and_location():
    job = normalize_raw_job({"company": "Acme", "title": "Manager, Commercial Strategy"})
    assert job.company == "Acme"
    assert job.location == ""
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.role_level == "Manager"
    assert job.role_family == "Commercial Strategy"


def test_normalize_url_removes_tracking_params():
    url = normalize_url("https://Example.com/jobs/123/?utm_source=linkedin&foo=bar")
    assert url == "https://example.com/jobs/123?foo=bar"


def test_parse_salary_range():
    assert parse_salary("$150k - $210k") == (150000, 210000)


def test_build_job_key_is_stable():
    first = build_job_key("Acme", "Senior Manager Strategy", "Plano TX")
    second = build_job_key("Acme", "Senior Manager Strategy", "Plano TX")
    assert first == second
