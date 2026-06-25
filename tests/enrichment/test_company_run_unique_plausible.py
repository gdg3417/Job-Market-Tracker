from __future__ import annotations

from src.enrichment.ats import AtsDiscoveryResult
from src.enrichment.company_run import run_company_ats_enrichment
from tests.enrichment.test_company_run import (
    NOW,
    FakeSheetClient,
    ats_config,
    candidate,
    direct_failure_queue,
    sparse_job,
)


def test_one_accepted_and_one_plausible_candidate_remains_ambiguous():
    job = sparse_job(
        "example-mixed-confidence",
        "Example Company",
        "Sr Manager, Strategic Planning",
        "Dallas, TX",
        "https://alerts.example/mixed-confidence",
    )
    client = FakeSheetClient([job], [direct_failure_queue(job)])

    def discovery(*_args, **_kwargs):
        return AtsDiscoveryResult(
            platform="greenhouse",
            status="success",
            candidates=[
                candidate("Example Company", "Senior Manager, Strategic Planning", "Dallas, TX", "accepted"),
                candidate("Example Company", "Manager, Strategic Planning", "Dallas, TX", "plausible"),
            ],
        )

    summary = run_company_ats_enrichment(
        client,
        configs=[ats_config()],
        discovery=discovery,
        priority_rules={},
        now=NOW,
    )

    assert summary.enriched == 0
    assert summary.ambiguous == 1
    assert client.tables["Jobs"][0]["enrichment_status"] == "ambiguous"
    assert client.tables["Jobs"][0]["canonical_url"] == "https://alerts.example/mixed-confidence"
    assert client.tables["Enrichment_Queue"][0]["status"] == "ambiguous"
    assert len(client.tables["Enrichment_Evidence"]) == 2
    assert all(row["accepted"] is False for row in client.tables["Enrichment_Evidence"])
