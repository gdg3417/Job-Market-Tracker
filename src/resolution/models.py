from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

RESOLUTION_STATES = {
    "resolved_authoritative",
    "resolved_probable",
    "ambiguous",
    "not_found",
    "blocked",
    "unsupported",
    "manual_override",
    "retryable_failure",
}

MERGEABLE_RESOLUTION_STATES = {"resolved_authoritative", "manual_override"}
MANUAL_DECISIONS = {"", "accept", "replace", "remove", "reject_automated"}

POSTING_RESOLUTION_FIELDS = [
    "resolution_id",
    "job_key",
    "resolution_state",
    "authoritative_url",
    "platform",
    "stable_identifier",
    "candidate_count",
    "match_confidence",
    "company_match",
    "title_match",
    "location_match",
    "requisition_match",
    "description_similarity",
    "posting_date_consistency",
    "source_domain_authority",
    "ats_identifier_consistency",
    "attempted_at",
    "resolved_at",
    "resolution_latency_seconds",
    "blocker_reason",
    "error_message",
    "manual_authoritative_url",
    "manual_resolution_decision",
    "manual_reviewer",
    "manual_review_date",
    "manual_notes",
    "created_at",
    "updated_at",
]

RESOLUTION_CANDIDATE_FIELDS = [
    "candidate_id",
    "resolution_id",
    "job_key",
    "discovery_order",
    "discovery_method",
    "source_type",
    "observed_url",
    "canonical_url",
    "platform",
    "stable_identifier",
    "requisition_id",
    "source_title",
    "source_company",
    "source_location",
    "posting_date",
    "description_excerpt",
    "company_match",
    "title_match",
    "location_match",
    "requisition_match",
    "description_similarity",
    "posting_date_consistency",
    "source_domain_authority",
    "ats_identifier_consistency",
    "match_confidence",
    "candidate_state",
    "accepted",
    "rejection_reason",
    "discovered_at",
    "updated_at",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else _int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "accepted"}


def resolution_id_for(job_key: str) -> str:
    digest = hashlib.sha256(str(job_key or "").strip().encode("utf-8")).hexdigest()[:20]
    return f"res_{digest}"


def candidate_id_for(job_key: str, canonical_url: str, discovery_method: str) -> str:
    material = "|".join(
        [
            str(job_key or "").strip().lower(),
            str(canonical_url or "").strip().lower(),
        ]
    )
    return f"rc_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


@dataclass(slots=True)
class PostingResolution:
    resolution_id: str = ""
    job_key: str = ""
    resolution_state: str = "not_found"
    authoritative_url: str = ""
    platform: str = ""
    stable_identifier: str = ""
    candidate_count: int = 0
    match_confidence: int | None = None
    company_match: int = 0
    title_match: int = 0
    location_match: int = 0
    requisition_match: int = 0
    description_similarity: int = 0
    posting_date_consistency: int = 0
    source_domain_authority: int = 0
    ats_identifier_consistency: int = 0
    attempted_at: str = ""
    resolved_at: str = ""
    resolution_latency_seconds: int = 0
    blocker_reason: str = ""
    error_message: str = ""
    manual_authoritative_url: str = ""
    manual_resolution_decision: str = ""
    manual_reviewer: str = ""
    manual_review_date: str = ""
    manual_notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not self.resolution_id and self.job_key:
            self.resolution_id = resolution_id_for(self.job_key)
        if self.resolution_state not in RESOLUTION_STATES:
            self.resolution_state = "not_found"
        self.candidate_count = _int(self.candidate_count)
        self.match_confidence = _optional_int(self.match_confidence)
        for field_name in (
            "company_match",
            "title_match",
            "location_match",
            "requisition_match",
            "description_similarity",
            "posting_date_consistency",
            "source_domain_authority",
            "ats_identifier_consistency",
            "resolution_latency_seconds",
        ):
            setattr(self, field_name, _int(getattr(self, field_name)))
        self.manual_resolution_decision = str(self.manual_resolution_decision or "").strip().lower()

    @property
    def mergeable(self) -> bool:
        return self.resolution_state in MERGEABLE_RESOLUTION_STATES

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {name: values.get(name, "") for name in POSTING_RESOLUTION_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "PostingResolution":
        return cls(**{name: row.get(name, "") for name in POSTING_RESOLUTION_FIELDS})


@dataclass(slots=True)
class ResolutionCandidate:
    candidate_id: str = ""
    resolution_id: str = ""
    job_key: str = ""
    discovery_order: int = 0
    discovery_method: str = ""
    source_type: str = ""
    observed_url: str = ""
    canonical_url: str = ""
    platform: str = ""
    stable_identifier: str = ""
    requisition_id: str = ""
    source_title: str = ""
    source_company: str = ""
    source_location: str = ""
    posting_date: str = ""
    description_excerpt: str = ""
    company_match: int = 0
    title_match: int = 0
    location_match: int = 0
    requisition_match: int = 0
    description_similarity: int = 0
    posting_date_consistency: int = 0
    source_domain_authority: int = 0
    ats_identifier_consistency: int = 0
    match_confidence: int = 0
    candidate_state: str = "discovered"
    accepted: bool = False
    rejection_reason: str = ""
    discovered_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not self.resolution_id and self.job_key:
            self.resolution_id = resolution_id_for(self.job_key)
        identity_url = self.canonical_url or self.observed_url
        if not self.candidate_id and self.job_key and identity_url:
            self.candidate_id = candidate_id_for(self.job_key, identity_url, self.discovery_method)
        self.discovery_order = _int(self.discovery_order)
        for field_name in (
            "company_match",
            "title_match",
            "location_match",
            "requisition_match",
            "description_similarity",
            "posting_date_consistency",
            "source_domain_authority",
            "ats_identifier_consistency",
            "match_confidence",
        ):
            setattr(self, field_name, max(0, min(100, _int(getattr(self, field_name)))))
        self.accepted = _bool(self.accepted)

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {name: values.get(name, "") for name in RESOLUTION_CANDIDATE_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ResolutionCandidate":
        return cls(**{name: row.get(name, "") for name in RESOLUTION_CANDIDATE_FIELDS})
