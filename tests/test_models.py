from pathlib import Path

from src.models import JobPosting, TargetProfile, days_between


def test_jobposting_from_dict_coerces_sheet_values():
    job = JobPosting.from_dict(
        {
            "company": "Acme",
            "title": "Director, Revenue Strategy",
            "salary_min": "170000",
            "salary_max": "220,000",
            "missed_count": "1",
            "status": "not_seen_once",
            "first_seen_date": "2026-06-01",
            "last_seen_date": "2026-06-16",
        }
    )
    assert job.salary_min == 170000
    assert job.salary_max == 220000
    assert job.missed_count == 1
    assert job.days_open == 15
    assert job.company_key == "acme"
    assert job.title_key == "director-revenue-strategy"


def test_job_lifecycle_helpers():
    job = JobPosting(first_seen_date="2026-06-01", last_seen_date="2026-06-01")
    job.mark_missed("2026-06-02")
    assert job.status == "not_seen_once"
    assert job.missed_count == 1
    job.mark_missed("2026-06-03")
    assert job.status == "likely_closed"
    assert job.missed_count == 2
    job.mark_seen("2026-06-04")
    assert job.status == "open"
    assert job.missed_count == 0
    job.mark_closed("2026-06-05")
    assert job.status == "confirmed_closed"
    assert job.closed_date == "2026-06-05"
    assert job.days_open == 4


def test_target_profile_loads_yaml():
    profile = TargetProfile.from_yaml(Path("config/target_profile.yml"))
    assert profile.profile_name == "commercial_leadership_p_and_l_pathway"
    assert profile.base_salary_floor == 140000
    assert "Remote" in profile.preferred_locations
    assert "Commercial Strategy" in profile.primary_role_families


def test_days_between_handles_missing_or_bad_dates():
    assert days_between("", "2026-06-16") == 0
    assert days_between("bad", "2026-06-16") == 0
    assert days_between("2026-06-10", "2026-06-16") == 6
