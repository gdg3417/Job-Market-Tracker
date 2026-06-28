from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ConnectorStatus = Literal[
    "success",
    "no_matching_jobs",
    "posting_not_found",
    "unauthorized",
    "blocked",
    "rate_limited",
    "temporary_server_failure",
    "parser_failure",
    "invalid_configuration",
    "unsupported_platform",
]

SUCCESS_STATUSES: set[str] = {"success", "no_matching_jobs"}
FAILURE_STATUSES: set[str] = {
    "posting_not_found",
    "unauthorized",
    "blocked",
    "rate_limited",
    "temporary_server_failure",
    "parser_failure",
    "invalid_configuration",
    "unsupported_platform",
}


@dataclass(frozen=True, slots=True)
class ConnectorLimits:
    max_pages: int = 3
    max_jobs: int = 100
    timeout_seconds: int = 20
    retry_count: int = 1
    backoff_seconds: float = 1.0
    rate_limit_per_minute: int = 30

    def bounded(self) -> "ConnectorLimits":
        return ConnectorLimits(
            max_pages=max(1, min(int(self.max_pages or 1), 10)),
            max_jobs=max(1, min(int(self.max_jobs or 1), 500)),
            timeout_seconds=max(1, min(int(self.timeout_seconds or 1), 60)),
            retry_count=max(0, min(int(self.retry_count or 0), 3)),
            backoff_seconds=max(0.0, min(float(self.backoff_seconds or 0), 10.0)),
            rate_limit_per_minute=max(1, min(int(self.rate_limit_per_minute or 1), 120)),
        )


@dataclass(frozen=True, slots=True)
class ConnectorError:
    category: ConnectorStatus
    message: str = ""
    http_status: int | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectorJob:
    requisition_id: str = ""
    canonical_url: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    additional_locations: tuple[str, ...] = ()
    posting_date: str = ""
    closing_date: str = ""
    employment_type: str = ""
    work_arrangement: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    description: str = ""
    department: str = ""
    posting_status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["additional_locations"] = list(self.additional_locations)
        return values


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    platform: str
    company_id: str = ""
    company_name: str = ""
    status: ConnectorStatus = "success"
    jobs: tuple[ConnectorJob, ...] = ()
    error: ConnectorError | None = None
    requests: int = 0
    pages_fetched: int = 0
    response_time_ms: int = 0
    rate_limited: bool = False
    source_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status in SUCCESS_STATUSES

    @property
    def failure(self) -> bool:
        return self.status in FAILURE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["jobs"] = [job.to_dict() for job in self.jobs]
        values["error"] = self.error.to_dict() if self.error else None
        return values


def normalize_status(value: Any) -> ConnectorStatus:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "empty": "no_matching_jobs",
        "not_found": "posting_not_found",
        "missing": "posting_not_found",
        "forbidden": "blocked",
        "access_blocked": "blocked",
        "http_401": "unauthorized",
        "http_403": "blocked",
        "http_404": "posting_not_found",
        "http_410": "posting_not_found",
        "http_429": "rate_limited",
        "server_error": "temporary_server_failure",
        "failed": "temporary_server_failure",
        "invalid_config": "invalid_configuration",
        "configured_only": "unsupported_platform",
    }
    candidate = aliases.get(normalized, normalized)
    if candidate in SUCCESS_STATUSES or candidate in FAILURE_STATUSES:
        return candidate  # type: ignore[return-value]
    return "parser_failure"
