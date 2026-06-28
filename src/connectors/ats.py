from __future__ import annotations

from time import perf_counter
from typing import Any

from src.connectors.models import ConnectorError, ConnectorJob, ConnectorLimits, ConnectorResult, normalize_status
from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult
from src.enrichment.ats import discover_ats_candidates as _legacy_discover_ats_candidates
from src.enrichment.company_config import CompanyEnrichmentConfig

STRUCTURED_CONNECTOR_PLATFORMS = {"greenhouse", "lever", "ashby", "smartrecruiters", "smart recruiters"}
CONFIGURED_ONLY_CONNECTOR_PLATFORMS = {
    "workday",
    "icims",
    "successfactors",
    "success factors",
    "phenom",
    "oracle",
    "oracle recruiting",
    "jobvite",
}


def normalize_platform(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def connector_scope(platform: Any) -> str:
    normalized = normalize_platform(platform)
    if normalized in STRUCTURED_CONNECTOR_PLATFORMS:
        return "structured"
    if normalized in CONFIGURED_ONLY_CONNECTOR_PLATFORMS:
        return "configured_only"
    return "unsupported"


def _job_from_candidate(candidate: AtsCandidate) -> ConnectorJob:
    return ConnectorJob(
        requisition_id=str(candidate.posting_id or ""),
        canonical_url=str(candidate.url or ""),
        title=str(candidate.title or ""),
        company=str(candidate.company or ""),
        location=str(candidate.location or ""),
        posting_date=str(candidate.posting_date or ""),
        closing_date=str(candidate.valid_through or ""),
        employment_type=str(candidate.employment_type or ""),
        work_arrangement=str(candidate.work_model or candidate.remote_status or "unknown"),
        salary_min=candidate.salary_min,
        salary_max=candidate.salary_max,
        currency=str(candidate.currency or "USD"),
        description=str(candidate.description_text or ""),
        posting_status="active",
        metadata={"platform": candidate.platform},
    )


def connector_result_from_ats(
    config: CompanyEnrichmentConfig,
    result: AtsDiscoveryResult,
    *,
    response_time_ms: int = 0,
    requests: int = 1,
) -> ConnectorResult:
    status = normalize_status(result.status)
    error = None
    if status not in {"success", "no_matching_jobs"}:
        retryable = status in {"rate_limited", "temporary_server_failure"}
        error = ConnectorError(
            category=status,
            message=str(result.error_message or ""),
            http_status=result.http_status,
            retryable=retryable,
        )
    return ConnectorResult(
        platform=normalize_platform(result.platform or config.ats_platform) or "unknown",
        company_id=config.company_id,
        company_name=config.canonical_name,
        status=status,
        jobs=tuple(_job_from_candidate(candidate) for candidate in result.candidates),
        error=error,
        requests=max(1, int(requests or 1)),
        pages_fetched=1 if result.status not in {"configured_only", "invalid_config"} else 0,
        response_time_ms=max(0, int(response_time_ms or 0)),
        rate_limited=status == "rate_limited",
        source_url=result.search_url or config.career_search_url or config.source_url,
        metadata={"legacy_status": result.status, "http_status": result.http_status},
    )


class StructuredAtsConnector:
    def __init__(self, *, limits: ConnectorLimits | None = None):
        self.limits = (limits or ConnectorLimits()).bounded()
        self._cache: dict[tuple[str, str, str, str], AtsDiscoveryResult] = {}

    def discover(
        self,
        config: CompanyEnrichmentConfig,
        *,
        expected_title: str = "",
        expected_location: str = "",
        session: Any | None = None,
        timeout_seconds: int | None = None,
    ) -> tuple[AtsDiscoveryResult, ConnectorResult]:
        platform = normalize_platform(config.ats_platform)
        scope = connector_scope(platform)
        if scope == "unsupported":
            raw = AtsDiscoveryResult(
                platform=platform or "unknown",
                status="invalid_config",
                error_message="Unsupported ATS platform for structured connector discovery",
                search_url=config.career_search_url,
            )
            return raw, connector_result_from_ats(config, raw, response_time_ms=0, requests=0)

        timeout = self.limits.timeout_seconds if timeout_seconds is None else min(timeout_seconds, self.limits.timeout_seconds)
        cache_key = (config.company_id or config.canonical_name, platform, expected_title, expected_location)
        started = perf_counter()
        if cache_key in self._cache:
            raw = self._cache[cache_key]
            elapsed = 0
        else:
            raw = _legacy_discover_ats_candidates(
                config,
                expected_title=expected_title,
                expected_location=expected_location,
                session=session,
                timeout_seconds=timeout,
            )
            raw.candidates = raw.candidates[: self.limits.max_jobs]
            self._cache[cache_key] = raw
            elapsed = round((perf_counter() - started) * 1000)
        return raw, connector_result_from_ats(config, raw, response_time_ms=elapsed, requests=1)


def discover_ats_candidates(
    config: CompanyEnrichmentConfig,
    *,
    expected_title: str = "",
    expected_location: str = "",
    session: Any | None = None,
    timeout_seconds: int = 20,
    limits: ConnectorLimits | None = None,
) -> AtsDiscoveryResult:
    connector = StructuredAtsConnector(limits=limits or ConnectorLimits(timeout_seconds=timeout_seconds))
    raw, _normalized = connector.discover(
        config,
        expected_title=expected_title,
        expected_location=expected_location,
        session=session,
        timeout_seconds=timeout_seconds,
    )
    return raw


def run_connector_discovery(
    config: CompanyEnrichmentConfig,
    *,
    expected_title: str = "",
    expected_location: str = "",
    session: Any | None = None,
    limits: ConnectorLimits | None = None,
) -> ConnectorResult:
    connector = StructuredAtsConnector(limits=limits)
    _raw, normalized = connector.discover(
        config,
        expected_title=expected_title,
        expected_location=expected_location,
        session=session,
    )
    return normalized
