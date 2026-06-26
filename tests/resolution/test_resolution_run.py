from __future__ import annotations

from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult
from src.enrichment.fetcher import EnrichmentFetchError
from src.enrichment.models import EnrichmentQueueItem
from src.models import JobPosting
from src.resolution.config import ResolutionSettings
from src.resolution.run import preview_posting_resolution, run_posting_resolution

NOW = "2026-06-26T12:00:00Z"


class FakeSheetClient:
    def __init__(self, jobs, queue_items, company_rows=None, resolutions=None):
        self.tables = {
            "Jobs": [job.to_dict() for job in jobs],
            "Job_Sources": [],
            "Enrichment_Queue": [item.to_dict() for item in queue_items],
            "Enrichment_Evidence": [],
            "Config_Companies": [dict(row) for row in (company_rows or [])],
            "Target_Companies": [],
            "Posting_Resolution": [dict(row) for row in (resolutions or [])],
            "Resolution_Candidates": [],
        }

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def read_jobs_with_row_numbers(self):
        return [(index + 2, JobPosting.from_dict(row)) for index, row in enumerate(self.tables["Jobs"])]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()


class BlockedLeadFetcher:
    def fetch(self, url):
        raise EnrichmentFetchError(
            "access_blocked",
            "lead page blocked",
            retryable=False,
            status_code=403,
            final_url=url,
        )


def sparse_job(job_key, company, title, location, lead_url, source_job_id=""):
    return JobPosting(
        job_key=job_key,
        company=company,
        title=title,
        location=location,
        canonical_url=lead_url,
        source_job_id=source_job_id,
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert",
        status="open",
        potential_priority_score=90,
        potential_priority="high",
        score_status="provisional",
        enrichment_status="pending",
        enrichment_priority="high",
    )


def queue_for(job):
    return EnrichmentQueueItem(
        enrichment_id=f"enr-{job.job_key}",
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        location=job.location,
        source_job_id=job.source_job_id,
        lead_url=job.canonical_url,
        priority="high",
        status="pending",
        current_stage="direct_url",
        created_at=NOW,
        updated_at=NOW,
    )


def config_row(company, aliases, domain):
    return {
        "company_name": company,
        "canonical_company_name": company,
        "company_aliases": aliases,
        "career_domain": domain,
        "career_search_url": f"https://{domain}/search-results",
        "ats_platform": "phenom",
        "enrichment_active": True,
    }


def ats_candidate(company, title, location, url, posting_id):
    return AtsCandidate(
        platform="phenom",
        posting_id=posting_id,
        company=company,
        title=title,
        location=location,
        url=url,
        description_text=(
            "Lead enterprise strategy and product planning. Partner with executive leaders, own growth initiatives, "
            "manage cross-functional operating cadence, and develop a team. Qualifications include eight years of experience."
        ),
        posting_date="2026-06-20",
    )


def test_topgolf_and_toyota_regressions_resolve_without_job_duplication():
    topgolf = sparse_job(
        "job-a4f80647216b",
        "Topgolf",
        "Sr Manager, Strategic Planning",
        "Dallas, TX",
        "https://www.linkedin.com/jobs/view/111",
    )
    toyota = sparse_job(
        "job-4988871ff583",
        "Toyota North America",
        "National Manager, Product",
        "Plano, TX",
        "https://www.linkedin.com/jobs/view/222",
    )
    rows = [
        config_row("Topgolf", "Topgolf Entertainment Group", "careers.topgolf.com"),
        config_row("Toyota Motor North America", "Toyota North America|Toyota", "careers.toyota.com"),
    ]
    client = FakeSheetClient([topgolf, toyota], [queue_for(topgolf), queue_for(toyota)], rows)

    def discovery(config, **_kwargs):
        if config.canonical_name == "Topgolf":
            candidate = ats_candidate(
                "Topgolf",
                "Senior Manager, Strategic Planning",
                "Dallas, TX",
                "https://careers.topgolf.com/us/en/job/TOP-100/senior-manager-strategic-planning",
                "TOP-100",
            )
        else:
            candidate = ats_candidate(
                "Toyota Motor North America",
                "National Manager, Product",
                "Plano, TX",
                "https://careers.toyota.com/us/en/job/TOY-200/national-manager-product",
                "TOY-200",
            )
        return AtsDiscoveryResult(platform="phenom", status="success", candidates=[candidate])

    result = run_posting_resolution(
        client,
        limit=2,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.resolution_succeeded == 2
    assert result.resolved_authoritative == 2
    assert len(client.tables["Jobs"]) == 2
    assert len(client.tables["Posting_Resolution"]) == 2
    assert len(client.tables["Enrichment_Evidence"]) == 2
    assert len(client.tables["Job_Sources"]) == 2
    assert all(row["resolution_state"] == "resolved_authoritative" for row in client.tables["Posting_Resolution"])
    assert all(row["accepted"] is True for row in client.tables["Enrichment_Evidence"])
    assert client.tables["Jobs"][0]["canonical_url"].startswith("https://careers.topgolf.com/")
    assert client.tables["Jobs"][1]["canonical_url"].startswith("https://careers.toyota.com/")


def test_ambiguous_candidates_are_preserved_without_merging():
    job = sparse_job("job-ambiguous", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
    )

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="phenom",
            status="success",
            candidates=[
                ats_candidate("Example Company", "Director, Strategy", "Dallas, TX", "https://careers.example.com/job/100/director-strategy", "100"),
                ats_candidate("Example Company", "Director, Strategy", "Dallas, TX", "https://careers.example.com/job/101/director-strategy", "101"),
            ],
        )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.ambiguous == 1
    assert result.manual_intervention_required == 1
    assert client.tables["Posting_Resolution"][0]["resolution_state"] == "ambiguous"
    assert client.tables["Posting_Resolution"][0]["authoritative_url"] == ""
    assert client.tables["Posting_Resolution"][0]["blocker_reason"] == "manual_review_required"
    assert len(client.tables["Enrichment_Evidence"]) == 0
    assert len(client.tables["Resolution_Candidates"]) == 2


def test_repeated_resolution_is_idempotent_for_evidence_sources_and_candidates():
    job = sparse_job("job-repeat", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
    )

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="phenom",
            status="success",
            candidates=[ats_candidate("Example Company", "Director, Strategy", "Dallas, TX", "https://careers.example.com/job/100/director-strategy", "100")],
        )

    first = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )
    second = run_posting_resolution(
        client,
        now="2026-06-26T13:00:00Z",
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert first.resolution_succeeded == 1
    assert second.resolution_succeeded == 1
    assert len(client.tables["Posting_Resolution"]) == 1
    assert len(client.tables["Resolution_Candidates"]) == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1
    assert len(client.tables["Job_Sources"]) == 1


def test_configured_only_regression_produces_stable_reviewable_reason():
    job = sparse_job("job-a4f80647216b", "Topgolf", "Sr Manager, Strategic Planning", "Dallas, TX", "https://linkedin.com/jobs/view/111")
    client = FakeSheetClient([job], [queue_for(job)], [config_row("Topgolf", "", "careers.topgolf.com")])

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="phenom",
            status="configured_only",
            error_message="No stable configured API adapter is available; the career search URL is retained for review",
            search_url="https://careers.topgolf.com/us/search-results",
        )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.unsupported == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["resolution_state"] == "unsupported"
    assert row["blocker_reason"] == "no_supported_enrichment_path"
    assert "No stable configured API adapter" in row["error_message"]

class HtmlFetcher:
    def __init__(self, pages):
        self.pages = dict(pages)

    def fetch(self, url):
        from src.enrichment.fetcher import FetchResult

        page = self.pages.get(url)
        if page is None:
            raise EnrichmentFetchError("not_found", "missing test page", retryable=False, status_code=404, final_url=url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text=page,
        )


def posting_html(title, company, location, canonical, identifier):
    import json

    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": location.split(",")[0], "addressRegion": location.split(",")[-1].strip()}},
        "description": "Lead strategy, product planning, executive operating cadence, growth initiatives, and a cross-functional team. Requirements include eight years of experience.",
        "datePosted": "2026-06-20",
        "identifier": {"@type": "PropertyValue", "name": company, "value": identifier},
        "url": canonical,
    }
    return f'<html><head><link rel="canonical" href="{canonical}"><script type="application/ld+json">{json.dumps(payload)}</script></head><body><h1>{title}</h1></body></html>'


def test_manual_override_is_durable_auditable_and_replaceable():
    job = sparse_job("job-manual", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    first_url = "https://careers.example.com/job/REQ-1/director-strategy"
    second_url = "https://careers.example.com/job/REQ-2/director-strategy"
    prior = {
        "resolution_id": "res-manual",
        "job_key": job.job_key,
        "resolution_state": "ambiguous",
        "manual_authoritative_url": first_url,
        "manual_resolution_decision": "accept",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "manual_notes": "Confirmed on employer site",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )
    fetcher = HtmlFetcher(
        {
            first_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", first_url, "REQ-1"),
            second_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", second_url, "REQ-2"),
        }
    )

    first = run_posting_resolution(
        client,
        now=NOW,
        fetcher=fetcher,
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "configured_only"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert first.manual_overrides == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["resolution_state"] == "manual_override"
    assert row["manual_reviewer"] == "Grayson"
    assert row["manual_notes"] == "Confirmed on employer site"
    assert row["authoritative_url"] == first_url

    row["manual_authoritative_url"] = second_url
    row["manual_resolution_decision"] = "replace"
    row["manual_notes"] = "Replacement confirmed"
    client.tables["Jobs"][0]["score_status"] = "provisional"
    client.tables["Jobs"][0]["enrichment_status"] = "pending"
    client.tables["Enrichment_Queue"][0]["status"] = "pending"
    client.tables["Enrichment_Queue"][0]["current_stage"] = "direct_url"

    second = run_posting_resolution(
        client,
        now="2026-06-26T13:00:00Z",
        fetcher=fetcher,
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "configured_only"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert second.manual_overrides == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["authoritative_url"] == second_url
    assert row["manual_resolution_decision"] == "replace"
    assert row["manual_notes"] == "Replacement confirmed"
    assert {candidate["canonical_url"] for candidate in client.tables["Resolution_Candidates"]} == {first_url, second_url}


def test_manual_override_can_be_explicitly_removed_without_erasing_candidate_audit():
    job = sparse_job("job-remove", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    manual_url = "https://careers.example.com/job/REQ-1/director-strategy"
    prior = {
        "resolution_id": "res-remove",
        "job_key": job.job_key,
        "resolution_state": "manual_override",
        "authoritative_url": manual_url,
        "manual_authoritative_url": manual_url,
        "manual_resolution_decision": "remove",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "manual_notes": "Posting was incorrect",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "configured_only", error_message="Adapter unavailable"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.unsupported == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["manual_authoritative_url"] == ""
    assert row["manual_resolution_decision"] == ""
    assert client.tables["Resolution_Candidates"][0]["candidate_state"] == "manual_removed"
    assert "Removed by Grayson" in client.tables["Resolution_Candidates"][0]["rejection_reason"]

class FailureFetcher:
    def __init__(self, error_type, retryable, status_code):
        self.error_type = error_type
        self.retryable = retryable
        self.status_code = status_code

    def fetch(self, url):
        raise EnrichmentFetchError(
            self.error_type,
            f"test {self.error_type}",
            retryable=self.retryable,
            status_code=self.status_code,
            final_url=url,
        )


def test_expired_authoritative_link_is_not_merged_or_treated_as_closure():
    url = "https://careers.example.com/job/expired"
    job = sparse_job("job-expired", "Example Company", "Director, Strategy", "Dallas, TX", url)
    client = FakeSheetClient([job], [queue_for(job)], [config_row("Example Company", "", "careers.example.com")])

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=FailureFetcher("not_found", False, 410),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.not_found == 1
    assert client.tables["Posting_Resolution"][0]["resolution_state"] == "not_found"
    assert client.tables["Jobs"][0]["status"] == "open"
    assert client.tables["Enrichment_Evidence"] == []


def test_temporary_authoritative_link_failure_is_retryable_and_does_not_merge():
    url = "https://careers.example.com/job/temporary"
    job = sparse_job("job-temporary", "Example Company", "Director, Strategy", "Dallas, TX", url)
    client = FakeSheetClient([job], [queue_for(job)], [config_row("Example Company", "", "careers.example.com")])

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=FailureFetcher("server_error", True, 503),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.retryable_failures == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["resolution_state"] == "retryable_failure"
    assert row["blocker_reason"] == "retry_scheduled"
    assert client.tables["Jobs"][0]["status"] == "open"
    assert client.tables["Enrichment_Evidence"] == []


def test_configured_employer_career_search_discovers_posting_links_with_bounded_fetching():
    job = sparse_job("job-career-search", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    search_url = "https://careers.example.com/search-results"
    posting_url = "https://careers.example.com/job/REQ-55/director-strategy"
    search_html = f'<html><body><a href="{posting_url}?utm_source=search">Director, Strategy</a><a href="/about">About</a></body></html>'
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
    )
    fetcher = HtmlFetcher(
        {
            search_url: search_html,
            posting_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", posting_url, "REQ-55"),
        }
    )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=fetcher,
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(
            career_search_link_budget=2,
            search_query_budget=0,
            external_page_budget=0,
        ),
        priority_rules={},
    )

    assert result.resolved_authoritative == 1
    assert result.candidates_discovered == 1
    candidate = client.tables["Resolution_Candidates"][0]
    assert candidate["discovery_method"] == "configured_employer_career_search"
    assert candidate["observed_url"] == posting_url
    assert candidate["canonical_url"] == posting_url


def test_manual_override_wins_when_automated_discovery_finds_the_same_url():
    job = sparse_job("job-manual-same", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    manual_url = "https://careers.example.com/job/REQ-9/director-strategy"
    prior = {
        "resolution_id": "res-manual-same",
        "job_key": job.job_key,
        "resolution_state": "ambiguous",
        "manual_authoritative_url": manual_url,
        "manual_resolution_decision": "accept",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )
    fetcher = HtmlFetcher({manual_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", manual_url, "REQ-9")})

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            "phenom",
            "success",
            candidates=[ats_candidate("Example Company", "Director, Strategy", "Dallas, TX", manual_url, "REQ-9")],
        )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=fetcher,
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.manual_overrides == 1
    assert len(client.tables["Resolution_Candidates"]) == 1
    candidate = client.tables["Resolution_Candidates"][0]
    assert set(candidate["discovery_method"].split("|")) == {"configured_ats_board", "manual_override"}
    assert client.tables["Posting_Resolution"][0]["resolution_state"] == "manual_override"


def test_invalid_or_unaudited_manual_decision_is_preserved_and_cannot_merge():
    job = sparse_job("job-manual-invalid", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    manual_url = "https://careers.example.com/job/REQ-10/director-strategy"
    prior = {
        "resolution_id": "res-manual-invalid",
        "job_key": job.job_key,
        "resolution_state": "ambiguous",
        "manual_authoritative_url": manual_url,
        "manual_resolution_decision": "approve_now",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.ambiguous == 1
    row = client.tables["Posting_Resolution"][0]
    assert row["manual_resolution_decision"] == "approve_now"
    assert "Invalid manual_resolution_decision" in row["error_message"]
    assert client.tables["Enrichment_Evidence"] == []


def test_manual_accept_requires_reviewer_and_review_date():
    job = sparse_job("job-manual-unaudited", "Example Company", "Director, Strategy", "Dallas, TX", "https://linkedin.com/jobs/view/1")
    manual_url = "https://careers.example.com/job/REQ-11/director-strategy"
    prior = {
        "resolution_id": "res-manual-unaudited",
        "job_key": job.job_key,
        "resolution_state": "ambiguous",
        "manual_authoritative_url": manual_url,
        "manual_resolution_decision": "accept",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=HtmlFetcher({manual_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", manual_url, "REQ-11")}),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.ambiguous == 1
    assert "require manual_reviewer" in client.tables["Posting_Resolution"][0]["error_message"]
    assert client.tables["Enrichment_Evidence"] == []


def test_low_confidence_candidate_is_reviewable_and_does_not_overwrite_job():
    original_url = "https://linkedin.com/jobs/view/low"
    job = sparse_job("job-low", "Example Company", "Director, Strategy", "Dallas, TX", original_url)
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
    )

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            "phenom",
            "success",
            candidates=[
                ats_candidate(
                    "Different Company",
                    "Director, Strategy",
                    "Dallas, TX",
                    "https://careers.example.com/job/999/director-strategy",
                    "999",
                )
            ],
        )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=BlockedLeadFetcher(),
        ats_discovery=discovery,
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.not_found == 1
    assert client.tables["Jobs"][0]["canonical_url"] == original_url
    assert client.tables["Posting_Resolution"][0]["authoritative_url"] == ""
    assert client.tables["Enrichment_Evidence"] == []
    assert client.tables["Resolution_Candidates"][0]["candidate_state"] == "rejected"
    assert "company_match_below_threshold" in client.tables["Resolution_Candidates"][0]["rejection_reason"]


def test_wrapped_lead_resolves_to_canonical_url_while_preserving_observed_url():
    canonical = "https://careers.example.com/job/REQ-77/director-strategy"
    wrapped = "https://safe.example.com/click?target=https%3A%2F%2Fcareers.example.com%2Fjob%2FREQ-77%2Fdirector-strategy%3Futm_source%3Demail"
    job = sparse_job("job-wrapped", "Example Company", "Director, Strategy", "Dallas, TX", wrapped, source_job_id="REQ-77")
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
    )
    fetcher = HtmlFetcher({canonical: posting_html("Director, Strategy", "Example Company", "Dallas, TX", canonical, "REQ-77")})

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=fetcher,
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.resolved_authoritative == 1
    candidate = client.tables["Resolution_Candidates"][0]
    assert candidate["observed_url"] == wrapped
    assert candidate["canonical_url"] == canonical
    source = client.tables["Job_Sources"][0]
    assert source["source_url"] == wrapped
    assert source["canonical_url"] == canonical


def test_manual_replacement_can_correct_a_previously_verified_job():
    job = sparse_job("job-verified-replace", "Example Company", "Director, Strategy", "Dallas, TX", "https://careers.example.com/job/old")
    job.score_status = "verified"
    new_url = "https://careers.example.com/job/REQ-12/director-strategy"
    prior = {
        "resolution_id": "res-verified-replace",
        "job_key": job.job_key,
        "resolution_state": "resolved_authoritative",
        "authoritative_url": job.canonical_url,
        "manual_authoritative_url": new_url,
        "manual_resolution_decision": "replace",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )

    result = run_posting_resolution(
        client,
        now=NOW,
        fetcher=HtmlFetcher({new_url: posting_html("Director, Strategy", "Example Company", "Dallas, TX", new_url, "REQ-12")}),
        ats_discovery=lambda *_args, **_kwargs: AtsDiscoveryResult("phenom", "empty"),
        settings=ResolutionSettings(career_search_link_budget=0, search_query_budget=0, external_page_budget=0),
        priority_rules={},
    )

    assert result.manual_overrides == 1
    assert client.tables["Posting_Resolution"][0]["authoritative_url"] == new_url
    assert client.tables["Jobs"][0]["canonical_url"] == new_url
    assert client.tables["Jobs"][0]["score_status"] == "verified"


def test_preview_includes_verified_job_with_pending_manual_resolution_action():
    job = sparse_job(
        "job-preview-manual",
        "Example Company",
        "Director, Strategy",
        "Dallas, TX",
        "https://careers.example.com/job/old",
    )
    job.score_status = "verified"
    prior = {
        "resolution_id": "res-preview-manual",
        "job_key": job.job_key,
        "resolution_state": "resolved_authoritative",
        "authoritative_url": job.canonical_url,
        "manual_authoritative_url": "https://careers.example.com/job/new",
        "manual_resolution_decision": "replace",
        "manual_reviewer": "Grayson",
        "manual_review_date": "2026-06-26",
        "created_at": NOW,
        "updated_at": NOW,
    }
    client = FakeSheetClient(
        [job],
        [queue_for(job)],
        [config_row("Example Company", "", "careers.example.com")],
        [prior],
    )

    preview = preview_posting_resolution(client)

    assert preview["eligible_jobs"] == 1
    assert preview["jobs"][0]["job_key"] == job.job_key
    assert preview["jobs"][0]["manual_resolution_decision"] == "replace"
