import requests

from src.sources.greenhouse import (
    fetch_greenhouse_board,
    fetch_greenhouse_jobs,
    greenhouse_company_rows,
    normalize_greenhouse_job,
    run_greenhouse_companies,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, error=None):
        self.payload = payload
        self.status_code = status_code
        self.error = error

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requested_urls = []

    def get(self, url, timeout):
        self.requested_urls.append((url, timeout))
        return self.response


def company_row(**overrides):
    values = {
        "company_name": "Acme Industrial",
        "source_type": "greenhouse",
        "source_slug": "acme",
        "source_url": "https://boards.greenhouse.io/acme",
        "ats_platform": "greenhouse",
        "industry_bucket": "manufacturing",
        "ownership_type": "PE-backed",
        "priority_tier": "Tier 1",
        "active": "TRUE",
    }
    values.update(overrides)
    return values


def greenhouse_payload():
    return {
        "jobs": [
            {
                "id": 123,
                "title": "Director, Commercial Strategy and Product Line Growth",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/123?gh_src=test",
                "location": {"name": "Plano, TX Hybrid"},
                "content": "<p>Own revenue growth, margin expansion, P&L pathway, operating cadence, and executive leadership updates.</p>",
                "departments": [{"name": "Strategy"}],
                "offices": [{"name": "Plano"}],
                "metadata": [{"name": "Compensation", "value": "$180,000 - $230,000"}],
            }
        ]
    }


def test_greenhouse_company_rows_filters_active_greenhouse_sources():
    rows = [
        company_row(),
        company_row(company_name="Inactive", active="FALSE"),
        company_row(company_name="Lever Co", source_type="lever", ats_platform="lever", source_url=""),
        company_row(company_name="Missing Slug", source_slug=""),
    ]

    filtered = greenhouse_company_rows(rows)

    assert len(filtered) == 1
    assert filtered[0]["company_name"] == "Acme Industrial"


def test_normalize_greenhouse_job_maps_job_fields():
    job = normalize_greenhouse_job(greenhouse_payload()["jobs"][0], company_row(), seen_date="2026-06-16")

    assert job.company == "Acme Industrial"
    assert job.title == "Director, Commercial Strategy and Product Line Growth"
    assert job.location == "Plano, TX Hybrid"
    assert job.source_primary == "greenhouse"
    assert job.source_job_id == "123"
    assert job.salary_min == 180000
    assert job.salary_max == 230000
    assert job.first_seen_date == "2026-06-16"
    assert "Departments: Strategy" in job.description_text


def test_fetch_greenhouse_jobs_scores_results():
    rules = {
        "score_scale": 100,
        "category_weights": {
            "fit_score": 15,
            "p_and_l_path_score": 20,
            "growth_ownership_score": 20,
            "executive_exposure_score": 15,
            "operating_cadence_score": 10,
            "comp_score": 10,
            "location_score": 5,
            "industry_match_score": 5,
        },
        "category_match_targets": {
            "p_and_l_path_score": 3,
            "growth_ownership_score": 3,
            "executive_exposure_score": 2,
            "operating_cadence_score": 2,
        },
        "role_level_keywords": {"Director": 15},
        "role_family_keywords": {"Commercial Strategy": ["commercial strategy"]},
        "role_family_fit": {"Commercial Strategy": 6},
        "team_leadership_keywords": [],
        "positive_keywords": {
            "p_and_l_path": ["P&L", "product line"],
            "growth_ownership": ["revenue growth", "margin expansion", "commercial strategy"],
            "executive_exposure": ["executive leadership"],
            "operating_cadence": ["operating cadence"],
        },
        "negative_keywords": {"hard_exclude": [], "penalties": {}},
        "alert_thresholds": {"immediate_review": 85, "strong_fit": 75, "track_only": 65},
        "alert_tiers": {"hard_exclude": "exclude", "below_track": "ignore"},
        "compensation": {
            "base_floor": 140000,
            "director_preferred_floor": 170000,
            "serious_total_comp": 180000,
            "strong_total_comp": 200000,
            "stretch_total_comp": 250000,
        },
        "location_scoring": {"hybrid_2_to_3_days": 5, "default": 1},
        "industry_fit": {"manufacturing": 5, "PE-backed": 5},
        "industry_exclusions": [],
    }
    session = FakeSession(FakeResponse(greenhouse_payload()))

    jobs = fetch_greenhouse_jobs(company_row(), scoring_rules=rules, session=session, seen_date="2026-06-16")

    assert len(jobs) == 1
    assert jobs[0].total_score > 0
    assert jobs[0].alert_tier in {"track_only", "strong_fit", "immediate_review"}
    assert session.requested_urls[0][0] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"


def test_fetch_greenhouse_board_handles_empty_board():
    result = fetch_greenhouse_board(company_row(), session=FakeSession(FakeResponse({"jobs": []})))

    assert result.status == "empty"
    assert result.records_found == 0
    assert result.jobs == []


def test_fetch_greenhouse_board_handles_invalid_slug_without_crashing():
    error = requests.HTTPError("404 Client Error")
    result = fetch_greenhouse_board(company_row(source_slug="bad-slug"), session=FakeSession(FakeResponse({}, status_code=404, error=error)))

    assert result.status == "failed"
    assert result.records_found == 0
    assert "404" in result.error_message


def test_run_greenhouse_companies_appends_run_rows():
    class FakeSheetClient:
        def __init__(self):
            self.run_records = []

        def append_run(self, record):
            self.run_records.append(record)

    sheet_client = FakeSheetClient()
    rows = [company_row()]
    session = FakeSession(FakeResponse(greenhouse_payload()))

    jobs, results = run_greenhouse_companies(rows, sheet_client=sheet_client, session=session)

    assert len(jobs) == 1
    assert len(results) == 1
    assert len(sheet_client.run_records) == 1
    assert sheet_client.run_records[0]["run_type"] == "sprint_5_greenhouse_source"
    assert sheet_client.run_records[0]["records_found"] == 1
