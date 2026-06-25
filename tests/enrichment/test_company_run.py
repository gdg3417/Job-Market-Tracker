from __future__ import annotations

from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult
from src.enrichment.company_config import company_config_from_row
from src.enrichment.company_run import run_company_ats_enrichment
from src.enrichment.models import EnrichmentQueueItem
from src.models import JobPosting

NOW = "2026-06-24T12:00:00Z"


class FakeSheetClient:
    def __init__(self, jobs, queue_items, company_rows=None):
        self.tables = {
            "Jobs": [job.to_dict() for job in jobs],
            "Enrichment_Queue": [item.to_dict() for item in queue_items],
            "Enrichment_Evidence": [],
            "Config_Companies": [dict(row) for row in (company_rows or [])],
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


def sparse_job(job_key: str, company: str, title: str, location: str, lead_url: str) -> JobPosting:
    return JobPosting(
        job_key=job_key,
        company=company,
        title=title,
        location=location,
        canonical_url=lead_url,
        source_job_id=job_key,
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert",
        status="open",
        potential_priority_score=85,
        potential_priority="high",
        score_status="provisional",
        enrichment_status="not_found",
        enrichment_priority="high",
    )


def direct_failure_queue(job: JobPosting) -> EnrichmentQueueItem:
    return EnrichmentQueueItem(
        enrichment_id=f"enr-{job.job_key}",
        job_key=job.job_key,
        company=job.company,
        title=job.title,
        location=job.location,
        source_job_id=job.source_job_id,
        lead_url=job.canonical_url,
        priority="high",
        status="not_found",
        current_stage="direct_url",
        attempt_count=1,
        created_at=NOW,
        updated_at=NOW,
    )


def ats_config(company: str = "Example Company"):
    return company_config_from_row(
        {
            "company_name": company,
            "canonical_company_name": company,
            "ats_platform": "greenhouse",
            "ats_board_token": "example",
            "career_search_url": "https://careers.example.com/search",
            "enrichment_active": True,
        }
    )


def candidate(company: str, title: str, location: str, suffix: str = "1") -> AtsCandidate:
    return AtsCandidate(
        platform="greenhouse",
        posting_id=suffix,
        company=company,
        title=title,
        location=location,
        url=f"https://boards.greenhouse.io/example/jobs/{suffix}",
        description_text=(
            "Responsibilities include leading strategic planning, owning growth initiatives, managing a team, "
            "and partnering with executive leadership. Qualifications include eight years of relevant experience."
        ),
        posting_date="2026-06-20",
    )


def test_exact_company_and_title_candidate_updates_existing_job_without_duplicate():
    job = sparse_job("example-1", "Example Company", "Sr Manager, Strategic Planning", "Dallas, TX", "https://alerts.example/1")
    client = FakeSheetClient([job], [direct_failure_queue(job)])

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[candidate("Example Company", "Senior Manager, Strategic Planning", "Dallas, TX")],
        )

    summary = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )

    assert summary.enriched == 1
    assert len(client.tables["Jobs"]) == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1
    assert client.tables["Jobs"][0]["enrichment_status"] == "enriched"
    assert client.tables["Jobs"][0]["canonical_url"].startswith("https://boards.greenhouse.io/")
    assert client.tables["Enrichment_Queue"][0]["current_stage"] == "company_ats"
    assert client.tables["Enrichment_Evidence"][0]["accepted"] is True


def test_incorrect_company_candidate_is_rejected_and_not_merged():
    job = sparse_job("example-2", "Example Company", "Sr Manager, Strategic Planning", "Dallas, TX", "https://alerts.example/2")
    client = FakeSheetClient([job], [direct_failure_queue(job)])

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[candidate("Different Company", "Senior Manager, Strategic Planning", "Dallas, TX")],
        )

    summary = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )

    assert summary.not_found == 1
    assert client.tables["Jobs"][0]["description_text"] == "Extracted from Gmail job alert"
    assert client.tables["Jobs"][0]["canonical_url"] == "https://alerts.example/2"
    assert client.tables["Enrichment_Evidence"][0]["accepted"] is False


def test_multiple_confident_candidates_are_marked_ambiguous():
    job = sparse_job("example-3", "Example Company", "Sr Manager, Strategic Planning", "Dallas, TX", "https://alerts.example/3")
    client = FakeSheetClient([job], [direct_failure_queue(job)])

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[
                candidate("Example Company", "Senior Manager, Strategic Planning", "Dallas, TX", "1"),
                candidate("Example Company", "Sr Manager, Strategic Planning", "Dallas, TX", "2"),
            ],
        )

    summary = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )

    assert summary.ambiguous == 1
    assert client.tables["Jobs"][0]["enrichment_status"] == "ambiguous"
    assert client.tables["Enrichment_Queue"][0]["status"] == "ambiguous"
    assert len(client.tables["Enrichment_Evidence"]) == 2
    assert all(row["accepted"] is False for row in client.tables["Enrichment_Evidence"])


def test_topgolf_and_toyota_receive_configured_company_search_paths():
    topgolf = sparse_job(
        "topgolf-1",
        "Topgolf",
        "Sr Manager, Strategic Planning",
        "Dallas, TX",
        "https://www.linkedin.com/jobs/view/topgolf-1",
    )
    toyota = sparse_job(
        "toyota-1",
        "Toyota North America",
        "National Manager, Product",
        "Plano, TX",
        "https://www.linkedin.com/jobs/view/toyota-1",
    )
    client = FakeSheetClient(
        [topgolf, toyota],
        [direct_failure_queue(topgolf), direct_failure_queue(toyota)],
    )

    summary = run_company_ats_enrichment(client, priority_rules={}, now=NOW)

    assert summary.company_ats_attempts == 2
    assert summary.configured_only == 2
    assert len(client.tables["Jobs"]) == 2
    queue_by_job = {row["job_key"]: row for row in client.tables["Enrichment_Queue"]}
    assert queue_by_job["topgolf-1"]["matched_url"] == "https://careers.topgolf.com/us/search-results"
    assert queue_by_job["toyota-1"]["matched_url"] == "https://careers.toyota.com/us/search-results"
    assert {row["current_stage"] for row in queue_by_job.values()} == {"company_ats"}


def test_repeated_company_stage_is_idempotent():
    job = sparse_job("example-4", "Example Company", "Sr Manager, Strategic Planning", "Dallas, TX", "https://alerts.example/4")
    client = FakeSheetClient([job], [direct_failure_queue(job)])
    calls = []

    def discovery(*_args, **_kwargs):
        calls.append(1)
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[candidate("Example Company", "Senior Manager, Strategic Planning", "Dallas, TX")],
        )

    first = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )
    second = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now="2026-06-24T13:00:00Z",
    )

    assert first.company_ats_attempts == 1
    assert second.company_ats_attempts == 0
    assert len(calls) == 1
    assert len(client.tables["Jobs"]) == 1
    assert len(client.tables["Enrichment_Evidence"]) == 1


def test_unexpected_failure_for_one_company_does_not_stop_other_jobs():
    bad = sparse_job("bad-1", "Bad Company", "Sr Manager, Strategic Planning", "Dallas, TX", "https://alerts.example/bad")
    good = sparse_job("good-1", "Good Company", "National Manager, Product", "Plano, TX", "https://alerts.example/good")
    client = FakeSheetClient([bad, good], [direct_failure_queue(bad), direct_failure_queue(good)])
    configs = [ats_config("Bad Company"), ats_config("Good Company")]

    def discovery(config, **_kwargs):
        if config.canonical_name == "Bad Company":
            raise RuntimeError("unexpected adapter failure")
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[candidate("Good Company", "National Manager, Product", "Plano, TX")],
        )

    summary = run_company_ats_enrichment(
        client,
        configs=configs,
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )

    assert summary.failures == 1
    assert summary.enriched == 1
    statuses = {row["job_key"]: row["enrichment_status"] for row in client.tables["Jobs"]}
    assert statuses == {"bad-1": "not_found", "good-1": "enriched"}
