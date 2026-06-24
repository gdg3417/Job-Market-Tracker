from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

ENRICHMENT_QUEUE_FIELDS = [
    "enrichment_id",
    "job_key",
    "company",
    "title",
    "location",
    "source_job_id",
    "lead_url",
    "priority",
    "status",
    "current_stage",
    "attempt_count",
    "next_attempt_at",
    "last_attempted_at",
    "matched_url",
    "match_confidence",
    "fields_recovered",
    "error_type",
    "error_message",
    "created_at",
    "updated_at",
]

ENRICHMENT_EVIDENCE_FIELDS = [
    "evidence_id",
    "job_key",
    "enrichment_id",
    "source_type",
    "source_url",
    "retrieved_at",
    "http_status",
    "canonical_url",
    "source_title",
    "source_company",
    "source_location",
    "description_text",
    "salary_min",
    "salary_max",
    "currency",
    "employment_type",
    "remote_status",
    "work_model",
    "posting_date",
    "valid_through",
    "team_leadership_text",
    "raw_content_hash",
    "match_confidence",
    "accepted",
    "rejection_reason",
    "created_at",
]

QUEUE_STATUSES = {
    "pending",
    "in_progress",
    "partial",
    "enriched",
    "ambiguous",
    "not_found",
    "retryable_failure",
    "permanent_failure",
    "closed",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "accepted"}


@dataclass(slots=True)
class EnrichmentQueueItem:
    enrichment_id: str = ""
    job_key: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    source_job_id: str = ""
    lead_url: str = ""
    priority: str = "high"
    status: str = "pending"
    current_stage: str = "direct_url"
    attempt_count: int = 0
    next_attempt_at: str = ""
    last_attempted_at: str = ""
    matched_url: str = ""
    match_confidence: int | None = None
    fields_recovered: str = ""
    error_type: str = ""
    error_message: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.attempt_count = _int(self.attempt_count)
        self.match_confidence = _optional_int(self.match_confidence)
        if self.status not in QUEUE_STATUSES:
            self.status = "pending"
        if self.priority not in {"high", "medium", "low"}:
            self.priority = "high"
        if not self.current_stage:
            self.current_stage = "direct_url"

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {field_name: values.get(field_name, "") for field_name in ENRICHMENT_QUEUE_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "EnrichmentQueueItem":
        return cls(**{field_name: row.get(field_name, "") for field_name in ENRICHMENT_QUEUE_FIELDS})


@dataclass(slots=True)
class EnrichmentEvidence:
    evidence_id: str = ""
    job_key: str = ""
    enrichment_id: str = ""
    source_type: str = "direct_url"
    source_url: str = ""
    retrieved_at: str = field(default_factory=utc_now_iso)
    http_status: int | None = None
    canonical_url: str = ""
    source_title: str = ""
    source_company: str = ""
    source_location: str = ""
    description_text: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = ""
    employment_type: str = ""
    remote_status: str = "unknown"
    work_model: str = "unknown"
    posting_date: str = ""
    valid_through: str = ""
    team_leadership_text: str = ""
    raw_content_hash: str = ""
    match_confidence: int | None = None
    accepted: bool = False
    rejection_reason: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.http_status = _optional_int(self.http_status)
        self.salary_min = _optional_int(self.salary_min)
        self.salary_max = _optional_int(self.salary_max)
        self.match_confidence = _optional_int(self.match_confidence)
        self.accepted = _bool(self.accepted)
        if not self.remote_status:
            self.remote_status = "unknown"
        if not self.work_model:
            self.work_model = "unknown"

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {field_name: values.get(field_name, "") for field_name in ENRICHMENT_EVIDENCE_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "EnrichmentEvidence":
        return cls(**{field_name: row.get(field_name, "") for field_name in ENRICHMENT_EVIDENCE_FIELDS})

    def recovered_fields(self) -> list[str]:
        candidates = {
            "canonical_url": self.canonical_url,
            "title": self.source_title,
            "company": self.source_company,
            "location": self.source_location,
            "description": self.description_text,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "employment_type": self.employment_type,
            "remote_status": "" if self.remote_status == "unknown" else self.remote_status,
            "work_model": "" if self.work_model == "unknown" else self.work_model,
            "posting_date": self.posting_date,
            "valid_through": self.valid_through,
            "team_leadership": self.team_leadership_text,
        }
        return [name for name, value in candidates.items() if value not in (None, "")]


@dataclass(frozen=True, slots=True)
class MatchResult:
    confidence: int
    outcome: str
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.outcome == "accepted"


@dataclass(slots=True)
class EnrichmentRunSummary:
    jobs_evaluated: int = 0
    jobs_enqueued: int = 0
    queue_existing: int = 0
    direct_attempts: int = 0
    enriched: int = 0
    partial: int = 0
    ambiguous: int = 0
    not_found: int = 0
    retryable_failures: int = 0
    permanent_failures: int = 0
    evidence_written: int = 0
    jobs_updated: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)
