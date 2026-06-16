from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


JOB_FIELDS = [
    "job_key", "company", "title", "location", "remote_status", "work_model",
    "commute_estimate_minutes", "salary_min", "salary_max", "currency",
    "total_comp_estimate", "source_primary", "source_job_id", "canonical_url",
    "description_text", "first_seen_date", "last_seen_date", "missed_count",
    "status", "closed_date", "days_open", "role_family", "role_level",
    "fit_score", "p_and_l_path_score", "growth_ownership_score",
    "executive_exposure_score", "operating_cadence_score", "comp_score",
    "location_score", "industry_match_score", "total_score", "alert_tier",
    "score_explanation", "created_at", "updated_at",
]


@dataclass(slots=True)
class JobPosting:
    job_key: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    remote_status: str = "unknown"
    work_model: str = "unknown"
    commute_estimate_minutes: int | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    total_comp_estimate: int | None = None
    source_primary: str = ""
    source_job_id: str = ""
    canonical_url: str = ""
    description_text: str = ""
    first_seen_date: str = field(default_factory=lambda: date.today().isoformat())
    last_seen_date: str = field(default_factory=lambda: date.today().isoformat())
    missed_count: int = 0
    status: str = "open"
    closed_date: str = ""
    days_open: int = 0
    role_family: str = "Unknown"
    role_level: str = "Unknown"
    fit_score: int = 0
    p_and_l_path_score: int = 0
    growth_ownership_score: int = 0
    executive_exposure_score: int = 0
    operating_cadence_score: int = 0
    comp_score: int = 0
    location_score: int = 0
    industry_match_score: int = 0
    total_score: int = 0
    alert_tier: str = "unscored"
    score_explanation: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        return {field_name: values.get(field_name, "") for field_name in JOB_FIELDS}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "JobPosting":
        allowed = {field_name: row.get(field_name, "") for field_name in JOB_FIELDS}
        return cls(**allowed)


@dataclass(slots=True)
class SourceRunResult:
    source_name: str
    status: str
    records_found: int = 0
    records_created: int = 0
    records_updated: int = 0
    error_message: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_row(self) -> dict[str, Any]:
        return asdict(self)
