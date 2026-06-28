from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

SPRINT_36_REVIEW_JOB_FIELDS = [
    "review_status",
    "reviewed_date",
    "reviewer",
    "interest_decision",
    "manual_priority",
    "manual_fit_rating",
    "manual_authoritative_url",
    "review_notes",
    "follow_up_date",
    "dismissal_reason",
    "dismissal_detail",
    "application_status",
    "application_date",
    "application_url",
    "resume_version",
    "cover_letter_version",
    "referral_or_contact",
    "interview_stage",
    "last_application_update",
    "next_action",
    "next_action_date",
    "manual_decision_conflict",
]

SPRINT_37_DECISION_JOB_FIELDS = [
    "base_salary_min",
    "base_salary_max",
    "salary_currency",
    "bonus_target_percent",
    "bonus_max_percent",
    "commission_estimate",
    "equity_or_lti_estimate",
    "sign_on_bonus",
    "estimated_total_comp_min",
    "estimated_total_comp_max",
    "compensation_source_type",
    "compensation_source_url",
    "compensation_observed_date",
    "compensation_confidence",
    "compensation_notes",
    "required_office_days_per_week",
    "travel_percentage",
    "relocation_required",
    "geographic_eligibility",
    "work_model_source",
    "work_model_confidence",
    "work_model_notes",
    "office_name",
    "office_street_address",
    "office_city",
    "office_state",
    "office_postal_code",
    "location_confidence",
    "estimated_one_way_distance",
    "estimated_one_way_travel_time",
    "commute_bucket",
    "commute_calculation_date",
    "commute_method",
    "commute_notes",
    "benefit_401k_match",
    "health_insurance_indicators",
    "paid_parental_leave",
    "pto",
    "pension",
    "tuition_reimbursement",
    "other_material_benefits",
    "benefits_source",
    "benefits_confidence",
    "benefits_notes",
    "compensation_improvement",
    "total_compensation_improvement",
    "work_model_improvement",
    "commute_improvement",
    "benefits_confidence_summary",
    "scope_p_and_l_modifier",
    "move_value_classification",
    "move_value_notes",
    "move_value_updated_at",
    "decision_evidence_conflict_notes",
]

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
    "lifecycle_last_checked_at", "lifecycle_next_check_at", "lifecycle_check_count",
    "lifecycle_miss_count", "lifecycle_last_evidence_key", "lifecycle_evidence_type",
    "lifecycle_evidence_url", "lifecycle_evidence_at", "lifecycle_reason",
    "lifecycle_last_authoritative_miss_date",
    *SPRINT_36_REVIEW_JOB_FIELDS,
    *SPRINT_37_DECISION_JOB_FIELDS,
]

VALID_JOB_STATUSES = {
    "open", "not_seen_once", "likely_closed", "confirmed_closed", "closed", "reopened", "expired",
}
VALID_POTENTIAL_PRIORITIES = {"high", "medium", "low", "excluded"}
VALID_SCORE_STATUSES = {"provisional", "partially_verified", "verified", "excluded"}
VALID_ENRICHMENT_STATUSES = {
    "not_required", "pending", "in_progress", "partial", "enriched", "ambiguous", "not_found",
    "retryable_failure", "permanent_failure", "closed",
}
VALID_REVIEW_STATUSES = {
    "not_reviewed", "review_now", "reviewing", "interested", "watch", "deferred", "dismissed",
    "applied", "interviewing", "offer", "rejected", "withdrawn", "closed",
}
VALID_APPLICATION_STATUSES = {
    "", "not_started", "drafting", "applied", "interviewing", "offer", "rejected", "withdrawn", "closed",
}
VALID_INTEREST_DECISIONS = {"", "interested", "watch", "deferred", "dismissed", "not_interested", "applied"}
VALID_DISMISSAL_REASONS = {
    "", "compensation_too_low", "commute_too_long", "on_site_requirement", "wrong_seniority",
    "role_too_junior", "role_too_senior", "too_much_fp_and_a", "weak_p_and_l_path",
    "weak_operating_scope", "industry_excluded", "company_not_attractive", "benefits_not_compelling",
    "role_closed", "duplicate", "recruiting_intermediary", "insufficient_improvement",
    "not_interested", "other",
}
VALID_COMPENSATION_SOURCE_TYPES = {
    "employer_posted", "recruiter_provided", "application_form", "government_disclosure",
    "trusted_external_estimate", "inferred_from_title", "user_entered", "unknown",
}
CONFIRMED_COMPENSATION_SOURCE_TYPES = {
    "employer_posted", "recruiter_provided", "application_form", "government_disclosure", "user_entered",
}
VALID_WORK_MODEL_VALUES = {"remote", "hybrid", "on_site", "unknown"}
VALID_EVIDENCE_CONFIDENCE = {"confirmed", "high", "medium", "low", "estimated", "unknown", "conflicting"}
VALID_COMMUTE_BUCKETS = {"", "under_15_minutes", "15_to_30_minutes", "30_to_45_minutes", "over_45_minutes", "unknown"}
VALID_MOVE_VALUE_CLASSIFICATIONS = {"clearly_better", "potentially_better", "lateral_or_uncertain", "worse", "insufficient_evidence"}
TERMINAL_JOB_STATUSES = {"confirmed_closed", "closed", "expired"}
OPTIONAL_INT_FIELDS = {
    "commute_estimate_minutes", "salary_min", "salary_max", "total_comp_estimate",
    "verified_total_score", "enrichment_match_confidence", "manual_priority", "manual_fit_rating",
    "base_salary_min", "base_salary_max", "bonus_target_percent", "bonus_max_percent",
    "commission_estimate", "equity_or_lti_estimate", "sign_on_bonus",
    "estimated_total_comp_min", "estimated_total_comp_max", "required_office_days_per_week",
    "travel_percentage", "estimated_one_way_distance", "estimated_one_way_travel_time",
}
INT_FIELDS = {
    "missed_count", "days_open", "fit_score", "p_and_l_path_score", "growth_ownership_score",
    "executive_exposure_score", "operating_cadence_score", "comp_score", "location_score",
    "industry_match_score", "total_score", "potential_priority_score", "evidence_completeness_score",
    "lifecycle_check_count", "lifecycle_miss_count",
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


def _normalize_choice(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_work_model(value: Any) -> str:
    text = _normalize_choice(value).replace("-", "_").replace(" ", "_")
    if text in {"onsite", "on_site", "in_office", "in_office_5_days", "office"}:
        return "on_site"
    if text in {"remote", "hybrid", "unknown"}:
        return text
    return "unknown" if not text else text


def _normalize_confidence(value: Any, default: str = "unknown") -> str:
    text = _normalize_choice(value)
    return text if text in VALID_EVIDENCE_CONFIDENCE else default


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
    lifecycle_last_checked_at: str = ""
    lifecycle_next_check_at: str = ""
    lifecycle_check_count: int = 0
    lifecycle_miss_count: int = 0
    lifecycle_last_evidence_key: str = ""
    lifecycle_evidence_type: str = ""
    lifecycle_evidence_url: str = ""
    lifecycle_evidence_at: str = ""
    lifecycle_reason: str = ""
    lifecycle_last_authoritative_miss_date: str = ""
    review_status: str = "not_reviewed"
    reviewed_date: str = ""
    reviewer: str = ""
    interest_decision: str = ""
    manual_priority: int | None = None
    manual_fit_rating: int | None = None
    manual_authoritative_url: str = ""
    review_notes: str = ""
    follow_up_date: str = ""
    dismissal_reason: str = ""
    dismissal_detail: str = ""
    application_status: str = ""
    application_date: str = ""
    application_url: str = ""
    resume_version: str = ""
    cover_letter_version: str = ""
    referral_or_contact: str = ""
    interview_stage: str = ""
    last_application_update: str = ""
    next_action: str = ""
    next_action_date: str = ""
    manual_decision_conflict: str = ""
    base_salary_min: int | None = None
    base_salary_max: int | None = None
    salary_currency: str = "USD"
    bonus_target_percent: int | None = None
    bonus_max_percent: int | None = None
    commission_estimate: int | None = None
    equity_or_lti_estimate: int | None = None
    sign_on_bonus: int | None = None
    estimated_total_comp_min: int | None = None
    estimated_total_comp_max: int | None = None
    compensation_source_type: str = "unknown"
    compensation_source_url: str = ""
    compensation_observed_date: str = ""
    compensation_confidence: str = "unknown"
    compensation_notes: str = ""
    required_office_days_per_week: int | None = None
    travel_percentage: int | None = None
    relocation_required: str = "unknown"
    geographic_eligibility: str = ""
    work_model_source: str = ""
    work_model_confidence: str = "unknown"
    work_model_notes: str = ""
    office_name: str = ""
    office_street_address: str = ""
    office_city: str = ""
    office_state: str = ""
    office_postal_code: str = ""
    location_confidence: str = "unknown"
    estimated_one_way_distance: int | None = None
    estimated_one_way_travel_time: int | None = None
    commute_bucket: str = ""
    commute_calculation_date: str = ""
    commute_method: str = ""
    commute_notes: str = ""
    benefit_401k_match: str = ""
    health_insurance_indicators: str = ""
    paid_parental_leave: str = ""
    pto: str = ""
    pension: str = ""
    tuition_reimbursement: str = ""
    other_material_benefits: str = ""
    benefits_source: str = ""
    benefits_confidence: str = "unknown"
    benefits_notes: str = ""
    compensation_improvement: str = ""
    total_compensation_improvement: str = ""
    work_model_improvement: str = ""
    commute_improvement: str = ""
    benefits_confidence_summary: str = ""
    scope_p_and_l_modifier: str = ""
    move_value_classification: str = "insufficient_evidence"
    move_value_notes: str = ""
    move_value_updated_at: str = ""
    decision_evidence_conflict_notes: str = ""

    def __post_init__(self) -> None:
        for field_name in OPTIONAL_INT_FIELDS:
            setattr(self, field_name, _coerce_optional_int(getattr(self, field_name)))
        for field_name in INT_FIELDS:
            setattr(self, field_name, _coerce_int(getattr(self, field_name)))
        if self.status not in VALID_JOB_STATUSES:
            self.status = "open"
        if not self.currency:
            self.currency = "USD"
        if not self.salary_currency:
            self.salary_currency = self.currency or "USD"
        if self.salary_currency:
            self.salary_currency = str(self.salary_currency).strip().upper()
        if self.base_salary_min is None and self.salary_min is not None:
            self.base_salary_min = self.salary_min
        if self.base_salary_max is None and self.salary_max is not None:
            self.base_salary_max = self.salary_max
        if self.estimated_total_comp_min is None and self.total_comp_estimate is not None:
            self.estimated_total_comp_min = self.total_comp_estimate
        if self.estimated_total_comp_max is None and self.total_comp_estimate is not None:
            self.estimated_total_comp_max = self.total_comp_estimate

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

        self.review_status = _normalize_choice(self.review_status) or "not_reviewed"
        if self.review_status not in VALID_REVIEW_STATUSES:
            self.review_status = "not_reviewed"
        self.interest_decision = _normalize_choice(self.interest_decision)
        if self.interest_decision not in VALID_INTEREST_DECISIONS:
            self.interest_decision = ""
        self.dismissal_reason = _normalize_choice(self.dismissal_reason)
        if self.dismissal_reason not in VALID_DISMISSAL_REASONS:
            self.dismissal_reason = "other" if self.dismissal_reason else ""
        self.application_status = _normalize_choice(self.application_status)
        if self.application_status not in VALID_APPLICATION_STATUSES:
            self.application_status = ""
        if not self.application_status and self.review_status in {"applied", "interviewing", "offer", "rejected", "withdrawn", "closed"}:
            self.application_status = self.review_status

        self.work_model = _normalize_work_model(self.work_model)
        self.remote_status = _normalize_choice(self.remote_status) or "unknown"
        if self.remote_status in {"onsite", "on-site", "in_office", "in office"}:
            self.remote_status = "on_site"
        self.compensation_source_type = _normalize_choice(self.compensation_source_type) or "unknown"
        if self.compensation_source_type not in VALID_COMPENSATION_SOURCE_TYPES:
            self.compensation_source_type = "unknown"
        self.compensation_confidence = _normalize_confidence(self.compensation_confidence)
        self.work_model_confidence = _normalize_confidence(self.work_model_confidence)
        self.location_confidence = _normalize_confidence(self.location_confidence)
        self.benefits_confidence = _normalize_confidence(self.benefits_confidence)
        self.commute_bucket = _normalize_choice(self.commute_bucket)
        if self.commute_bucket not in VALID_COMMUTE_BUCKETS:
            self.commute_bucket = "unknown"
        self.move_value_classification = _normalize_choice(self.move_value_classification) or "insufficient_evidence"
        if self.move_value_classification not in VALID_MOVE_VALUE_CLASSIFICATIONS:
            self.move_value_classification = "insufficient_evidence"
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

    def mark_seen(self, seen_date: str | None = None, *, allow_reopen: bool = False) -> None:
        previous_status = self.status
        self.last_seen_date = seen_date or today_iso()
        self.missed_count = 0
        if previous_status in TERMINAL_JOB_STATUSES:
            if allow_reopen:
                self.status = "reopened"
                self.closed_date = ""
            else:
                self.days_open = days_between(self.first_seen_date, self.closed_date or self.last_seen_date)
                self.refresh_updated_at()
                return
        elif previous_status in {"not_seen_once", "likely_closed", "reopened"}:
            self.status = "open"
            self.closed_date = ""
        self.days_open = days_between(self.first_seen_date, self.last_seen_date)
        self.refresh_updated_at()

    def mark_missed(self, run_date: str | None = None) -> None:
        if self.status in TERMINAL_JOB_STATUSES:
            self.days_open = days_between(self.first_seen_date, self.closed_date or run_date or today_iso())
            self.refresh_updated_at()
            return
        self.missed_count += 1
        if self.missed_count == 1:
            self.status = "not_seen_once"
        elif self.missed_count >= 2:
            self.status = "likely_closed"
        self.days_open = days_between(self.first_seen_date, run_date or today_iso())
        self.refresh_updated_at()

    def mark_closed(self, closed_date: str | None = None) -> None:
        self.status = "confirmed_closed"
        self.closed_date = closed_date or today_iso()
        self.days_open = days_between(self.first_seen_date, self.closed_date)
        self.refresh_updated_at()

    def mark_expired(self, expired_date: str | None = None) -> None:
        self.status = "expired"
        self.closed_date = expired_date or today_iso()
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
