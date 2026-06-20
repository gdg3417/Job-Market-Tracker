import requests

from src.sources.static_pages import (
    extract_static_page_candidates,
    fetch_static_page_board,
    fetch_static_page_jobs,
    run_static_page_companies,
    search_filter_terms,
    static_page_company_rows,
)


class FakeResponse:
    def __init__(self, text, status_code=200, error=None):
        self.text = text
        self.status_code = status_code
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requested_urls = []
        self.requested_headers = []

    def get(self, url, timeout, headers=None):
        self.requested_urls.append((url, timeout))
        self.requested_headers.append(headers or {})
        return self.response


def company_row(**overrides):
    values = {
        "company_name": "Acme Industrial",
        "source_type": "static_page",
        "source_slug": "",
        "source_url": "https://www.acme.example/careers",
        "ats_platform": "custom",
        "location_focus": "Plano, TX",
        "industry_bucket": "manufacturing",
        "ownership_type": "PE-backed",
        "priority_tier": "Tier 1",
        "active": "TRUE",
    }
    values.update(overrides)
    return values


def search_rows():
    return [
        {
            "bucket": "Commercial Strategy",
            "include_keywords": "commercial strategy, product line, revenue growth",
            "exclude_keywords": "intern, staff accountant",
            "active": "TRUE",
        }
    ]


def html_payload():
    return """
    <html>
      <body>
        <a href="/careers">Careers</a>
        <a href="/jobs/123-director-commercial-strategy">Director, Commercial Strategy and Product Line Growth</a>
        <a href="/jobs/456-staff-accountant">Staff Accountant</a>
        <a href="/benefits">Benefits</a>
        <a href="https://linkedin.com/company/acme/jobs">LinkedIn Jobs</a>
        <div class="opening"><a href="/positions/789">View job</a><span>Manager, Revenue Growth</span></div>
      </body>
    </html>
    """


def json_ld_html_payload():
    return """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Director, Commercial Strategy",
            "description": "Own commercial strategy, revenue growth, product line performance, executive leadership updates, and operating cadence.",
            "url": "https://www.acme.example/jobs/director-commercial-strategy-12345",
            "jobLocation": {
              "@type": "Place",
              "address": {
                "@type": "PostalAddress",
                "addressLocality": "Plano",
                "addressRegion": "TX",
                "addressCountry": "US"
              }
            }
          }
        </script>
      </head>
      <body></body>
    </html>
    """


def scoring_rules():
    return {
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
        "role_level_keywords": {"Director": 15, "Manager": 8},
        "role_family_keywords": {"Commercial Strategy": ["commercial strategy"], "Product Line Management": ["product line"]},
        "role_family_fit": {"Commercial Strategy": 6, "Product Line Management": 6},
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
        "location_scoring": {"hybrid_2_to_3_days": 5, "default": 1, "Plano, TX": 5},
        "industry_fit": {"manufacturing": 5, "PE-backed": 5},
        "industry_exclusions": [],
    }


def test_static_page_company_rows_filters_active_static_sources():
    rows = [
        company_row(),
        company_row(company_name="Inactive", active="FALSE"),
        company_row(company_name="Greenhouse Co", source_type="greenhouse", ats_platform="greenhouse", source_url="https://boards.greenhouse.io/acme"),
        company_row(company_name="Lever Co", source_type="lever", ats_platform="lever", source_url="https://jobs.lever.co/acme"),
        company_row(company_name="Missing URL", source_url=""),
    ]

    filtered = static_page_company_rows(rows)

    assert len(filtered) == 1
    assert filtered[0]["company_name"] == "Acme Industrial"


def test_static_page_company_rows_respects_sprint18_source_audit_fields():
    rows = [
        company_row(company_name="Static Direct", ingestion_mode="static_direct", source_quality="success"),
        company_row(company_name="Gmail Only", ingestion_mode="gmail_only", source_quality="too_noisy"),
        company_row(company_name="Manual Review", ingestion_mode="manual_review_only", source_quality="needs_manual_url_correction"),
        company_row(company_name="Disabled", ingestion_mode="disabled", source_quality="disable_recommended"),
        company_row(company_name="Failed Static", ingestion_mode="static_direct", source_quality="failed"),
        company_row(company_name="Built In", source_type="job_board", ats_platform="job_board", source_url="https://builtin.com/jobs/dallas/operations"),
        company_row(company_name="Google Alerts", source_type="job_alert", ats_platform="gmail_alert", source_url="https://www.google.com/search?q=commercial+strategy+manager+jobs+Dallas"),
    ]

    filtered = static_page_company_rows(rows)

    assert [row["company_name"] for row in filtered] == ["Static Direct"]


def test_static_page_company_rows_accepts_common_non_greenhouse_ats_sources():
    rows = [
        company_row(
            company_name="Workday Co",
            source_type="workday",
            ats_platform="workday",
            source_url="https://acme.wd1.myworkdayjobs.com/en-US/acme",
        )
    ]

    filtered = static_page_company_rows(rows)

    assert len(filtered) == 1
    assert filtered[0]["company_name"] == "Workday Co"


def test_search_filter_terms_uses_config_searches_when_present():
    include_terms, exclude_terms = search_filter_terms(search_rows())

    assert "commercial strategy" in include_terms
    assert "product line" in include_terms
    assert "staff accountant" in exclude_terms


def test_extract_static_page_candidates_filters_unrelated_and_excluded_links():
    candidates = extract_static_page_candidates(
        html_payload(),
        "https://www.acme.example/careers",
        company_row=company_row(),
        search_rows=search_rows(),
    )

    titles = [candidate.title for candidate in candidates]
    urls = [candidate.url for candidate in candidates]
    assert len(candidates) == 2
    assert "Director, Commercial Strategy and Product Line Growth" in titles
    assert all("staff-accountant" not in url for url in urls)
    assert all("benefits" not in url for url in urls)
    assert all("linkedin" not in url for url in urls)


def test_extract_static_page_candidates_reads_json_ld_job_postings():
    candidates = extract_static_page_candidates(
        json_ld_html_payload(),
        "https://www.acme.example/careers",
        company_row=company_row(),
        search_rows=search_rows(),
    )

    assert len(candidates) == 1
    assert candidates[0].title == "Director, Commercial Strategy"
    assert candidates[0].source_kind == "json_ld"
    assert candidates[0].confidence == "high"
    assert candidates[0].location == "Plano, TX, US"
    assert "operating cadence" in candidates[0].description


def test_fetch_static_page_jobs_scores_and_marks_confidence():
    session = FakeSession(FakeResponse(html_payload()))

    jobs = fetch_static_page_jobs(
        company_row(),
        scoring_rules=scoring_rules(),
        search_rows=search_rows(),
        session=session,
        seen_date="2026-06-16",
    )

    assert len(jobs) == 2
    assert jobs[0].source_primary == "static_page"
    assert jobs[0].first_seen_date == "2026-06-16"
    assert jobs[0].total_score > 0
    assert "static_confidence=" in jobs[0].score_explanation
    assert session.requested_urls[0][0] == "https://www.acme.example/careers"


def test_fetch_static_page_jobs_scores_json_ld_description():
    session = FakeSession(FakeResponse(json_ld_html_payload()))

    jobs = fetch_static_page_jobs(
        company_row(),
        scoring_rules=scoring_rules(),
        search_rows=search_rows(),
        session=session,
        seen_date="2026-06-16",
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Director, Commercial Strategy"
    assert jobs[0].location == "Plano, TX, US"
    assert "operating cadence" in jobs[0].description_text
    assert "static_source_kind=json_ld" in jobs[0].score_explanation


def test_fetch_static_page_board_handles_fetch_error_without_crashing():
    error = requests.HTTPError("500 Server Error")
    result = fetch_static_page_board(
        company_row(),
        session=FakeSession(FakeResponse("", status_code=500, error=error)),
    )

    assert result.status == "failed"
    assert result.records_found == 0
    assert "500" in result.error_message


def test_run_static_page_companies_appends_run_rows():
    class FakeSheetClient:
        def __init__(self):
            self.run_records = []

        def append_run(self, record):
            self.run_records.append(record)

    sheet_client = FakeSheetClient()
    session = FakeSession(FakeResponse(html_payload()))

    jobs, results = run_static_page_companies(
        [company_row()],
        search_rows=search_rows(),
        sheet_client=sheet_client,
        session=session,
    )

    assert len(jobs) == 2
    assert len(results) == 1
    assert len(sheet_client.run_records) == 1
    assert sheet_client.run_records[0]["run_type"] == "sprint_10_static_page_source"
    assert sheet_client.run_records[0]["records_found"] == 2
