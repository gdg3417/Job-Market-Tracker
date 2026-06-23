from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml


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
    "potential_priority_score", "potential_priority", "potential_priority_reason",
    "evidence_completeness_score", "score_status", "verified_total_score",
    "verified_alert_tier", "enrichment_status", "enrichment_priority",
    "enrichment_last_attempted_at", "enrichment_completed_at",
    "enrichment_source_url", "enrichment_match_confidence",
]

VALID_JOB_STATUSES = {"open", "not_seen_once", "likely_closed", "confirmed_closed", "reopened"}
VALID_POTENTIAL_PRIORITIES = {"high", "medium", "low", "excluded"}
VALID_SCORE_STATUSES = {"provisional", "partially_verified", "verified", "excluded"}
VALID_ENRICHMENT_STATUSES = {
    "not_required",
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
OPTIONAL_INT_FIELDS = {
    "commute_estimate_minutes", "salary_min", "salary_max", "total_comp_estimate",
    "verified_total_score", "enrichment_match_confidence",
}
INT_FIELDS = {
    "missed_count", "days_open", "fit_score", "p_and_l_path_score",
    "growth_ownership_score", "executive_exposure_score", "operating_cadence_score",
    "comp_score", "location_score", "industry_match_score", "total_score",
    "potential_priority_score", "evidence_completeness_score",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    return date.today().isoformat()


def parse_iso_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def days_between(start: Any, end: Any) -> int:
    start_date = parse_iso_date(start)
    end_date = parse_iso_date(end) or date.today()
    if start_date is None:
        return 0
    return max(0, (end_date - start_date).days)


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any, default: int = 0) -> int:
    coerced = _coerce_optional_int(value)
    return default if coerced is None else coerced


def normalize_key_part(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower().replace("&", " and ")
    import re

    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


@dataclass(slots=True)
class TargetProfile:
    profile_name: str = ""
    primary_positioning: str = ""
    search_intent: str = ""
    current_role: dict[str, Any] = field(default_factory=dict)
    minimum_move_logic: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] = field(default_factory=dict)
    role_families: dict[str, list[str]] = field(default_factory=dict)
    locations: dict[str, list[str]] = field(default_factory=dict)
    commute_scoring: dict[str, Any] = field(default_factory=dict)
    work_model_preferences: dict[str, Any] = field(default_factory=dict)
    industry_priorities: list[str] = field(default_factory=list)
    industry_exclusions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "TargetProfile":
        return cls(
            profile_name=str(values.get("profile_name", "")),
            primary_positioning=str(values.get("primary_positioning", "")),
            search_intent=str(values.get("search_intent", "")),
            current_role=dict(values.get("current_role") or {}),
            minimum_move_logic=dict(values.get("minimum_move_logic") or {}),
            compensation=dict(values.get("compensation") or {}),
            role_families=dict(values.get("role_families") or {}),
            locations=dict(values.get("locations") or {}),
            commute_scoring=dict(values.get("commute_scoring") or {}),
            work_model_preferences=dict(values.get("work_model_preferences") or {}),
            industry_priorities=list(values.get("industry_priorities") or []),
            industry_exclusions=list(values.get("industry_exclusions") or []),
            raw=dict(values),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TargetProfile":
        with Path(path).open("r", encoding="utf-8") as file:
            return cls.from_dict(yaml.safe_load(file) or {})

    @property
    def preferred_locations(self) -> list[str]:
        return list((self.locations or {}).get("preferred", []))

    @property
    def primary_role_families(self) -> list[str]:
        return list((self.role_families or {}).get("primary", []))

    @property
    def base_salary_floor(self) -> int:
        return _coerce_int(self.compensation.get("absolute_base_floor"), 140000)


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
    first_seen_date: str = field(default_factory=today_iso)
    last_seen_date: str = field(default_factory=today_iso)
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
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    potential_priority_score: int = 0
    potential_priority: str = ""
    potential_priority_reason: str = ""
    evidence_completeness_score: int = 0
    score_status: str = ""
    verified_total_score: int | None = None
    verified_alert_tier: str = ""
    enrichment_status: str = ""
    enrichment_priority: str = ""
    enrichment_last_attempted_at: str = ""
    enrichment_completed_at: str = ""
    enrichment_source_url: str = ""
    enrichment_match_confidence: int | None = None

    def __post_init__(self) -> None:
        for field_name in OPTIONAL_INT_FIELDS:
            setattr(self, field_name, _coerce_optional_int(getattr(self, field_name)))
        for field_name in INT_FIELDS:
            setattr(self, field_name, _coerce_int(getattr(self, field_name)))
        if self.status not in VALID_JOB_STATUSES:
            self.status = "open"
        if not self.currency:
            self.currency = "USD"

        explanation = str(self.score_explanation or "").lower()
        if self.potential_priority not in VALID_POTENTIAL_PRIORITIES:
            self.potential_priority = "excluded" if self.alert_tier == "exclude" else "low"
        if self.score_status not in VALID_SCORE_STATUSES:
            if self.alert_tier == "exclude" or "hard_exclude=true" in explanation:
                self.score_status = "excluded"
            elif "manual_review=true" in explanation:
                self.score_status = "provisional"
            elif self.alert_tier not in {"", "unscored"}:
                self.score_status = "verified"
            else:
                self.score_status = "provisional"
        if self.score_status == "verified":
            if self.verified_total_score is None:
                self.verified_total_score = self.total_score
            if not self.verified_alert_tier:
                self.verified_alert_tier = self.alert_tier
        elif self.score_status == "excluded":
            if self.verified_total_score is None:
                self.verified_total_score = 0
            if not self.verified_alert_tier:
                self.verified_alert_tier = "exclude"
        if self.enrichment_status not in VALID_ENRICHMENT_STATUSES:
            self.enrichment_status = "not_required"
        if self.enrichment_priority not in {"", "high", "medium", "low"}:
            self.enrichment_priority = ""
        self.days_open = days_between(self.first_seen_date, self.closed_date or self.last_seen_date)

    @property
    def company_key(self) -> str:
        return normalize_key_part(self.company)

    @property
    def title_key(self) -> str:
        return normalize_key_part(self.title)

    @property
    def location_key(self) -> str:
        return normalize_key_part(self.location)

    def refresh_updated_at(self) -> None:
        self.updated_at = utc_now_iso()

    def mark_seen(self, seen_date: str | None = None) -> None:
        previous_status = self.status
        self.last_seen_date = seen_date or today_iso()
        self.missed_count = 0
        if previous_status == "confirmed_closed":
            self.status = "reopened"
        elif previous_status in {"not_seen_once", "likely_closed", "reopened"}:
            self.status = "open"
        self.closed_date = ""
        self.days_open = days_between(self.first_seen_date, self.last_seen_date)
        self.refresh_updated_at()

    def mark_missed(self, run_date: str | None = None) -> None:
        self.missed_count += 1
        if self.missed_count == 1:
            self.status = "not_seen_once"
        elif self.missed_count >= 2 and self.status != "confirmed_closed":
            self.status = "likely_closed"
        self.days_open = days_between(self.first_seen_date, run_date or today_iso())
        self.refresh_updated_at()

    def mark_closed(self, closed_date: str | None = None) -> None:
        self.status = "confirmed_closed"
        self.closed_date = closed_date or today_iso()
        self.days_open = days_between(self.first_seen_date, self.closed_date)
        self.refresh_updated_at()

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
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)

    def to_row(self) -> dict[str, Any]:
        return asdict(self)
