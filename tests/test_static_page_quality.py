from src.sources.static_pages import extract_static_page_candidates, static_page_company_rows


def row(company_name="Acme", source_url="https://www.acme.example/careers", source_type="static_page", source_slug=""):
    return {
        "company_name": company_name,
        "source_type": source_type,
        "source_slug": source_slug,
        "source_url": source_url,
        "ats_platform": "custom",
        "location_focus": "Dallas, TX",
        "industry_bucket": "manufacturing",
        "active": "TRUE",
    }


def test_noisy_job_boards_are_not_static_company_sources():
    rows = [
        row(),
        row(company_name="The Ladders", source_url="https://www.theladders.com/jobs/search-jobs?keywords=strategy&location=Dallas"),
        row(company_name="Google Jobs", source_url="https://www.jobs.google.com/search?q=jobs+near+me"),
    ]

    filtered = static_page_company_rows(rows)

    assert [item["company_name"] for item in filtered] == ["Acme"]


def test_static_page_rejects_generic_board_navigation_links():
    html = """
    <html><body>
      <a href="https://www.theladders.com/jobs/search-jobs?keywords=project+manager&location=Dallas">Job Search Search Jobs</a>
      <a href="https://www.acme.example/jobs/director-commercial-strategy-12345">Director, Commercial Strategy</a>
    </body></html>
    """

    candidates = extract_static_page_candidates(html, "https://www.acme.example/careers", company_row=row())

    assert len(candidates) == 1
    assert candidates[0].title == "Director, Commercial Strategy"
    assert "theladders" not in candidates[0].url
