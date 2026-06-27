from __future__ import annotations

from src.connectors.ats import connector_result_from_ats, connector_scope, discover_ats_candidates, run_connector_discovery
from src.connectors.models import ConnectorLimits, normalize_status
from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult
from src.enrichment.company_config import CompanyEnrichmentConfig


def config(platform="greenhouse"):
    return CompanyEnrichmentConfig(
        company_id="example",
        company_name="Example Co",
        canonical_company_name="Example Co",
        ats_platform=platform,
        ats_board_token="example",
        career_search_url="https://boards.greenhouse.io/example",
    )


def test_connector_status_normalization_contract():
    assert normalize_status("success") == "success"
    assert normalize_status("empty") == "no_matching_jobs"
    assert normalize_status("invalid_config") == "invalid_configuration"
    assert normalize_status("configured_only") == "unsupported_platform"
    assert normalize_status("http_429") == "rate_limited"
    assert normalize_status("unparseable") == "parser_failure"


def test_connector_scope_identifies_structured_and_configured_only_platforms():
    assert connector_scope("greenhouse") == "structured"
    assert connector_scope("Smart Recruiters") == "structured"
    assert connector_scope("phenom") == "configured_only"
    assert connector_scope("unknown") == "unsupported"


def test_ats_connector_result_uses_normalized_contract():
    result = AtsDiscoveryResult(
        platform="greenhouse",
        status="success",
        candidates=[
            AtsCandidate(
                platform="greenhouse",
                posting_id="REQ-1",
                title="Director, Strategy",
                company="Example Co",
                location="Dallas, TX",
                url="https://boards.greenhouse.io/example/jobs/REQ-1",
                description_text="Lead strategy and operations.",
                salary_min=150000,
                salary_max=180000,
                currency="USD",
                employment_type="Full-time",
                remote_status="hybrid",
                work_model="hybrid",
                posting_date="2026-06-20",
                valid_through="2026-07-20",
            )
        ],
        http_status=200,
        search_url="https://boards.greenhouse.io/example",
    )

    normalized = connector_result_from_ats(config(), result, response_time_ms=123, requests=1)

    assert normalized.status == "success"
    assert normalized.success is True
    assert normalized.jobs[0].requisition_id == "REQ-1"
    assert normalized.jobs[0].salary_min == 150000
    assert normalized.jobs[0].work_arrangement == "hybrid"
    assert normalized.response_time_ms == 123


def test_connector_result_preserves_normalized_errors():
    result = AtsDiscoveryResult(
        platform="greenhouse",
        status="invalid_config",
        error_message="Missing board token",
        search_url="https://boards.greenhouse.io/example",
    )

    normalized = connector_result_from_ats(config(), result)

    assert normalized.status == "invalid_configuration"
    assert normalized.failure is True
    assert normalized.error is not None
    assert normalized.error.category == "invalid_configuration"
    assert normalized.error.retryable is False


def test_discover_wrapper_enforces_job_limit(monkeypatch):
    candidates = [
        AtsCandidate(platform="greenhouse", posting_id=f"REQ-{index}", title="Director", url=f"https://example.com/{index}")
        for index in range(5)
    ]

    def fake_discover(*_args, **_kwargs):
        return AtsDiscoveryResult(platform="greenhouse", status="success", candidates=list(candidates))

    monkeypatch.setattr("src.connectors.ats._legacy_discover_ats_candidates", fake_discover)

    result = discover_ats_candidates(config(), limits=ConnectorLimits(max_jobs=2))

    assert len(result.candidates) == 2


def test_run_connector_discovery_returns_unsupported_for_unknown_platform():
    result = run_connector_discovery(config("unknown-platform"))

    assert result.status == "invalid_configuration"
    assert result.error is not None
    assert result.error.category == "invalid_configuration"
