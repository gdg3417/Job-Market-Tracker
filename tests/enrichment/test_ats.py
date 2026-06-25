from __future__ import annotations

from src.enrichment.ats import (
    ASHBY_URL_TEMPLATE,
    SMARTRECRUITERS_DETAIL_URL_TEMPLATE,
    SMARTRECRUITERS_LIST_URL_TEMPLATE,
    discover_ats_candidates,
)
from src.enrichment.company_config import company_config_from_row
from src.sources.greenhouse import GREENHOUSE_URL_TEMPLATE
from src.sources.lever import LEVER_URL_TEMPLATE


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.payloads[url]


def config(platform: str, *, token: str = "", company_id: str = ""):
    return company_config_from_row(
        {
            "company_name": "Example Company",
            "canonical_company_name": "Example Company",
            "ats_platform": platform,
            "ats_board_token": token,
            "ats_company_id": company_id,
            "career_search_url": "https://careers.example.com/search",
            "enrichment_active": True,
        }
    )


def test_greenhouse_board_fixture_produces_description_candidate():
    url = GREENHOUSE_URL_TEMPLATE.format(slug="example")
    session = FakeSession(
        {
            url: FakeResponse(
                {
                    "jobs": [
                        {
                            "id": 101,
                            "title": "Senior Manager, Strategic Planning",
                            "absolute_url": "https://boards.greenhouse.io/example/jobs/101",
                            "location": {"name": "Dallas, TX"},
                            "content": "<p>Lead strategic planning and growth initiatives across the company.</p>",
                        }
                    ]
                }
            )
        }
    )

    result = discover_ats_candidates(config("greenhouse", token="example"), session=session)

    assert result.status == "success"
    assert len(result.candidates) == 1
    assert result.candidates[0].title == "Senior Manager, Strategic Planning"
    assert "Lead strategic planning" in result.candidates[0].description_text
    assert result.candidates[0].url.endswith("/101")


def test_lever_board_fixture_produces_description_candidate():
    url = LEVER_URL_TEMPLATE.format(slug="example")
    session = FakeSession(
        {
            url: FakeResponse(
                [
                    {
                        "id": "lever-1",
                        "text": "National Manager, Product",
                        "hostedUrl": "https://jobs.lever.co/example/lever-1",
                        "descriptionPlain": "Own product strategy, operating results, and cross-functional execution.",
                        "categories": {"location": "Plano, TX", "commitment": "Full-time"},
                    }
                ]
            )
        }
    )

    result = discover_ats_candidates(config("lever", token="example"), session=session)

    assert result.status == "success"
    assert len(result.candidates) == 1
    assert result.candidates[0].posting_id == "lever-1"
    assert result.candidates[0].location == "Plano, TX"
    assert "Own product strategy" in result.candidates[0].description_text


def test_ashby_board_fixture_produces_description_candidate():
    url = ASHBY_URL_TEMPLATE.format(token="example")
    session = FakeSession(
        {
            url: FakeResponse(
                {
                    "jobs": [
                        {
                            "id": "ashby-1",
                            "title": "Director, Business Operations",
                            "location": "Dallas, TX",
                            "jobUrl": "https://jobs.ashbyhq.com/example/ashby-1",
                            "descriptionHtml": "<p>Lead business operations and executive planning.</p>",
                            "employmentType": "FullTime",
                            "publishedAt": "2026-06-20T12:00:00Z",
                        }
                    ]
                }
            )
        }
    )

    result = discover_ats_candidates(config("ashby", token="example"), session=session)

    assert result.status == "success"
    assert result.candidates[0].posting_id == "ashby-1"
    assert result.candidates[0].posting_date == "2026-06-20"
    assert result.candidates[0].description_text == "Lead business operations and executive planning."


def test_smartrecruiters_fixture_fetches_ranked_detail_description():
    list_url = SMARTRECRUITERS_LIST_URL_TEMPLATE.format(company_id="example-company")
    detail_url = SMARTRECRUITERS_DETAIL_URL_TEMPLATE.format(company_id="example-company", posting_id="sr-1")
    session = FakeSession(
        {
            list_url: FakeResponse(
                {
                    "content": [
                        {
                            "id": "sr-1",
                            "name": "Senior Manager, Strategic Planning",
                            "location": {"city": "Dallas", "region": "TX", "country": "US"},
                            "ref": detail_url,
                        }
                    ]
                }
            ),
            detail_url: FakeResponse(
                {
                    "id": "sr-1",
                    "name": "Senior Manager, Strategic Planning",
                    "postingUrl": "https://jobs.smartrecruiters.com/ExampleCompany/sr-1",
                    "location": {"city": "Dallas", "region": "TX", "country": "US"},
                    "company": {"name": "Example Company"},
                    "releasedDate": "2026-06-21T10:00:00Z",
                    "jobAd": {
                        "sections": {
                            "jobDescription": {"text": "<p>Lead enterprise strategy and growth planning.</p>"},
                            "qualifications": {"text": "<p>Eight years of relevant experience.</p>"},
                        }
                    },
                }
            ),
        }
    )

    result = discover_ats_candidates(
        config("smartrecruiters", company_id="example-company"),
        expected_title="Sr Manager, Strategic Planning",
        expected_location="Dallas, TX",
        session=session,
    )

    assert result.status == "success"
    assert len(result.candidates) == 1
    assert "Lead enterprise strategy" in result.candidates[0].description_text
    assert "Eight years" in result.candidates[0].description_text
    assert result.candidates[0].url.startswith("https://jobs.smartrecruiters.com/")


def test_dynamic_configured_platform_does_not_scrape_landing_page():
    session = FakeSession({})

    result = discover_ats_candidates(config("phenom"), session=session)

    assert result.status == "configured_only"
    assert result.candidates == []
    assert result.search_url == "https://careers.example.com/search"
    assert session.calls == []
