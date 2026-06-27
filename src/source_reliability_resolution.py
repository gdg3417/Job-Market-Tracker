from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.connectors.models import ConnectorError, ConnectorJob, ConnectorResult
from src.enrichment.company_config import CompanyEnrichmentConfig, load_company_configs, resolve_company_config
from src.models import JobPosting
from src.resolution.models import PostingResolution
from src.source_reliability import SourceHealthState, observe_connector_result

RESOLUTION_TO_CONNECTOR_STATUS = {
    "resolved_authoritative": "success",
    "manual_override": "success",
    "resolved_probable": "success",
    "ambiguous": "success",
    "not_found": "posting_not_found",
    "blocked": "blocked",
    "unsupported": "unsupported_platform",
    "retryable_failure": "temporary_server_failure",
}


@dataclass(slots=True)
class SourceReliabilityResolutionSummary:
    resolution_rows_evaluated: int = 0
    resolution_rows_with_config: int = 0
    source_health_rows_observed: int = 0
    skipped_without_config: int = 0
    skipped_unattempted: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _records(sheet_client: Any, worksheet: str) -> list[tuple[int, dict[str, Any]]]:
    try:
        if hasattr(sheet_client, "read_records_with_row_numbers"):
            return list(sheet_client.read_records_with_row_numbers(worksheet))
        return [(index + 2, row) for index, row in enumerate(sheet_client.read_records(worksheet))]
    except Exception as exc:
        if exc.__class__.__name__ in {"WorksheetNotFound", "KeyError"}:
            return []
        raise


def _jobs(sheet_client: Any) -> dict[str, JobPosting]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        rows = sheet_client.read_jobs_with_row_numbers()
        return {job.job_key: job for _, job in rows if job.job_key}
    return {
        job.job_key: job
        for _, row in _records(sheet_client, "Jobs")
        if (job := JobPosting.from_dict(row)).job_key
    }


def _platform_for(config: CompanyEnrichmentConfig | None, resolution: PostingResolution) -> str:
    return str(
        (config.ats_platform if config else "")
        or resolution.platform
        or "unconfigured"
    ).strip().lower()


def _source_url_for(config: CompanyEnrichmentConfig | None, resolution: PostingResolution) -> str:
    if config is None:
        return resolution.authoritative_url
    return (
        config.career_search_url
        or config.source_url
        or (f"https://{config.career_domain}" if config.career_domain else "")
        or resolution.authoritative_url
    )


def _connector_status_for(resolution: PostingResolution) -> str:
    return RESOLUTION_TO_CONNECTOR_STATUS.get(str(resolution.resolution_state or "").strip(), "parser_failure")


def _connector_jobs_for(job: JobPosting | None, resolution: PostingResolution, status: str) -> tuple[ConnectorJob, ...]:
    if status not in {"success", "no_matching_jobs"}:
        return ()
    if job is None:
        return ()
    return (
        ConnectorJob(
            requisition_id=resolution.stable_identifier,
            canonical_url=resolution.authoritative_url,
            title=job.title,
            company=job.company,
            location=job.location,
            posting_status="active",
            metadata={"resolution_state": resolution.resolution_state},
        ),
    )


def _connector_result_from_resolution(
    config: CompanyEnrichmentConfig | None,
    job: JobPosting | None,
    resolution: PostingResolution,
) -> ConnectorResult:
    platform = _platform_for(config, resolution)
    source_url = _source_url_for(config, resolution)
    status = _connector_status_for(resolution)
    error = None
    if status not in {"success", "no_matching_jobs"}:
        retryable = status in {"temporary_server_failure", "rate_limited"}
        error = ConnectorError(
            category=status,  # type: ignore[arg-type]
            message=resolution.error_message or resolution.blocker_reason or resolution.resolution_state,
            retryable=retryable,
        )
    return ConnectorResult(
        platform=platform,
        company_id=config.company_id if config else "",
        company_name=(config.canonical_name if config else "") or (job.company if job else ""),
        status=status,  # type: ignore[arg-type]
        jobs=_connector_jobs_for(job, resolution, status),
        error=error,
        requests=1,
        pages_fetched=1 if resolution.attempted_at else 0,
        response_time_ms=int(resolution.resolution_latency_seconds or 0),
        source_url=source_url,
        metadata={
            "job_key": resolution.job_key,
            "resolution_state": resolution.resolution_state,
            "candidate_count": resolution.candidate_count,
            "blocker_reason": resolution.blocker_reason,
        },
    )


def refresh_source_health_from_resolutions(
    sheet_client: Any,
    *,
    observed_at: str = "",
    attempted_at: str = "",
    job_key: str = "",
) -> SourceReliabilityResolutionSummary:
    summary = SourceReliabilityResolutionSummary()
    jobs_by_key = _jobs(sheet_client)
    configs = load_company_configs(sheet_client)
    seen_sources: set[str] = set()

    for _, row in _records(sheet_client, "Posting_Resolution"):
        resolution = PostingResolution.from_dict(row)
        if job_key and resolution.job_key != job_key:
            continue
        if not resolution.attempted_at:
            summary.skipped_unattempted += 1
            continue
        if attempted_at and resolution.attempted_at != attempted_at:
            continue
        summary.resolution_rows_evaluated += 1
        job = jobs_by_key.get(resolution.job_key)
        config = resolve_company_config(job.company, configs) if job is not None else None
        if config is None:
            summary.skipped_without_config += 1
            continue
        summary.resolution_rows_with_config += 1
        connector_result = _connector_result_from_resolution(config, job, resolution)
        source_key = "|".join([connector_result.company_id, connector_result.company_name, connector_result.platform, connector_result.source_url])
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        state = observe_connector_result(
            sheet_client,
            connector_result,
            jobs_accepted=len(connector_result.jobs),
            observed_at=observed_at or resolution.attempted_at,
        )
        if isinstance(state, SourceHealthState) and state.source_health_id:
            summary.source_health_rows_observed += 1
    return summary
