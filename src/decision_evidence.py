from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from src.models import CONFIRMED_COMPENSATION_SOURCE_TYPES, JobPosting, TargetProfile, VALID_COMPENSATION_SOURCE_TYPES, today_iso, utc_now_iso

POSITIVE_PNL_TERMS = ("p&l", "profit and loss", "business unit", "general manager", "product line", "category", "segment", "commercial strategy", "revenue strategy")
COMPENSATION_SOURCE_RANK = {"unknown": 0, "inferred_from_title": 1, "trusted_external_estimate": 2, "government_disclosure": 5, "application_form": 5, "employer_posted": 6, "recruiter_provided": 6, "user_entered": 7}
WORK_MODEL_RANK = {"": 0, "unknown": 0, "trusted_external_estimate": 1, "employer_posted": 4, "recruiter_provided": 4, "application_form": 4, "user_entered": 5}
MOVE_VALUE_LABELS = {"clearly_better": "Clearly better", "potentially_better": "Potentially better", "lateral_or_uncertain": "Lateral or uncertain", "worse": "Worse", "insufficient_evidence": "Insufficient evidence"}


@dataclass(frozen=True, slots=True)
class MoveCriteria:
    current_base_compensation: int = 140000
    current_bonus_target_percent: int = 15
    senior_manager_base_floor: int = 150000
    director_preferred_base_floor: int = 170000
    serious_move_total_comp: int = 180000
    target_total_comp_low: int = 200000
    target_total_comp_high: int = 240000
    current_one_way_commute_miles: int = 30
    current_one_way_commute_minutes: int = 60

    @property
    def current_total_compensation(self) -> int:
        return round(self.current_base_compensation * (1 + self.current_bonus_target_percent / 100))

    @classmethod
    def from_target_profile(cls, profile: TargetProfile | None) -> "MoveCriteria":
        if profile is None:
            return cls()
        current_role = profile.current_role or {}
        compensation = profile.compensation or {}
        daily_commute = _optional_int(current_role.get("commute_daily_time_minutes"))
        return cls(
            current_base_compensation=_optional_int(current_role.get("base_salary")) or 140000,
            current_bonus_target_percent=_optional_int(current_role.get("bonus_target_percent")) or 15,
            senior_manager_base_floor=_optional_int(compensation.get("senior_manager_floor")) or 150000,
            director_preferred_base_floor=_optional_int(compensation.get("director_preferred_floor")) or 170000,
            serious_move_total_comp=_optional_int(compensation.get("minimum_serious_move_total_comp")) or 180000,
            target_total_comp_low=_optional_int(compensation.get("strong_total_comp_low")) or 200000,
            target_total_comp_high=_optional_int(compensation.get("strong_total_comp_high")) or 240000,
            current_one_way_commute_miles=_optional_int(current_role.get("commute_one_way_miles")) or 30,
            current_one_way_commute_minutes=round(daily_commute / 2) if daily_commute else 60,
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_compensation_source_type(value: Any) -> str:
    source = _normalize_text(value) or "unknown"
    return source if source in VALID_COMPENSATION_SOURCE_TYPES else "unknown"


def is_confirmed_compensation(source_type: Any) -> bool:
    return normalize_compensation_source_type(source_type) in CONFIRMED_COMPENSATION_SOURCE_TYPES


def compensation_status(source_type: Any, *, has_amount: bool) -> str:
    if not has_amount:
        return "unknown"
    return "confirmed" if is_confirmed_compensation(source_type) else "estimated"


def parse_compensation_text(text: str) -> dict[str, Any]:
    amounts = []
    for match in re.finditer(r"\$?\s*(\d{2,3}(?:,\d{3})?|\d{2,3})\s*(k|K)?", text or ""):
        number = int(match.group(1).replace(",", ""))
        if match.group(2) or number < 1000:
            number *= 1000
        if 50000 <= number <= 500000:
            amounts.append(number)
    if not amounts:
        return {"base_salary_min": None, "base_salary_max": None, "salary_currency": "USD"}
    if len(amounts) == 1:
        return {"base_salary_min": amounts[0], "base_salary_max": amounts[0], "salary_currency": "USD"}
    return {"base_salary_min": min(amounts[:2]), "base_salary_max": max(amounts[:2]), "salary_currency": "USD"}


def estimate_total_compensation(base_min: Any, base_max: Any, *, bonus_target_percent: Any = None, bonus_max_percent: Any = None, commission_estimate: Any = None, equity_or_lti_estimate: Any = None, sign_on_bonus: Any = None) -> tuple[int | None, int | None]:
    low_base = _optional_int(base_min)
    high_base = _optional_int(base_max) or low_base
    if low_base is None and high_base is None:
        return None, None
    if low_base is None:
        low_base = high_base
    target_bonus = _optional_int(bonus_target_percent) or 0
    max_bonus = _optional_int(bonus_max_percent)
    fixed = sum(_optional_int(value) or 0 for value in [commission_estimate, equity_or_lti_estimate, sign_on_bonus])
    low_total = round(low_base * (1 + target_bonus / 100)) + fixed
    high_total = round((high_base or low_base) * (1 + ((max_bonus if max_bonus is not None else target_bonus) / 100))) + fixed
    return low_total, high_total


def infer_work_model(text: str) -> str:
    normalized = str(text or "").lower()
    if "remote" in normalized:
        return "remote"
    if "hybrid" in normalized:
        return "hybrid"
    if any(term in normalized for term in ["on-site", "onsite", "in office", "in-office", "5 days"]):
        return "on_site"
    return "unknown"


def calculate_commute_bucket(*, travel_time_minutes: Any = None, distance_miles: Any = None) -> str:
    minutes = _optional_int(travel_time_minutes)
    miles = _optional_int(distance_miles)
    if minutes is not None:
        if minutes < 15:
            return "under_15_minutes"
        if minutes <= 30:
            return "15_to_30_minutes"
        if minutes <= 45:
            return "30_to_45_minutes"
        return "over_45_minutes"
    if miles is not None:
        if miles < 8:
            return "under_15_minutes"
        if miles <= 20:
            return "15_to_30_minutes"
        if miles <= 35:
            return "30_to_45_minutes"
        return "over_45_minutes"
    return "unknown"


def _visible_score(job: JobPosting) -> int:
    return job.verified_total_score if job.verified_total_score is not None else job.total_score


def _role_base_floor(job: JobPosting, criteria: MoveCriteria) -> int:
    text = f"{job.title} {job.role_level}".lower()
    if "director" in text:
        return criteria.director_preferred_base_floor
    if "sr" in text or "senior manager" in text or "senior" in text:
        return criteria.senior_manager_base_floor
    return criteria.current_base_compensation


def _has_pnl_scope(job: JobPosting) -> bool:
    text = f"{job.title} {job.role_family} {job.description_text} {job.score_explanation}".lower()
    return job.p_and_l_path_score >= 14 or any(term in text for term in POSITIVE_PNL_TERMS)


def _best_total_comp(job: JobPosting) -> int | None:
    return max([value for value in [job.estimated_total_comp_max, job.estimated_total_comp_min, job.total_comp_estimate] if value is not None], default=None)


def _best_base(job: JobPosting) -> int | None:
    return max([value for value in [job.base_salary_max, job.base_salary_min, job.salary_max, job.salary_min] if value is not None], default=None)


def _compensation_improvement(job: JobPosting, criteria: MoveCriteria) -> str:
    best_total = _best_total_comp(job)
    best_base = _best_base(job)
    if best_total is None and best_base is None:
        return "unknown"
    if best_total is not None:
        if best_total >= criteria.target_total_comp_low:
            return "target_total_comp"
        if best_total >= criteria.serious_move_total_comp:
            return "serious_move_total_comp"
        if best_total < criteria.current_total_compensation:
            return "below_current_total_comp"
    if best_base is not None:
        if best_base >= _role_base_floor(job, criteria):
            return "meets_role_base_floor"
        if best_base < criteria.current_base_compensation:
            return "below_current_base"
    return "lateral_or_uncertain"


def _work_model_improvement(job: JobPosting) -> str:
    if job.work_model == "remote":
        return "strong_improvement"
    if job.work_model == "hybrid":
        return "neutral" if job.required_office_days_per_week is not None and job.required_office_days_per_week >= 4 else "improvement"
    if job.work_model == "on_site":
        return "penalty" if job.required_office_days_per_week is not None and job.required_office_days_per_week >= 5 else "neutral"
    return "unknown"


def _commute_improvement(job: JobPosting) -> str:
    bucket = job.commute_bucket or calculate_commute_bucket(travel_time_minutes=job.estimated_one_way_travel_time or job.commute_estimate_minutes, distance_miles=job.estimated_one_way_distance)
    if bucket in {"under_15_minutes", "15_to_30_minutes"}:
        return "improvement"
    if bucket == "30_to_45_minutes":
        return "current_like"
    if bucket == "over_45_minutes":
        return "worse"
    return "unknown"


def calculate_move_value(job: JobPosting, criteria: MoveCriteria | None = None) -> dict[str, Any]:
    criteria = criteria or MoveCriteria()
    has_amount = _best_total_comp(job) is not None or _best_base(job) is not None
    comp_status = compensation_status(job.compensation_source_type, has_amount=has_amount)
    comp = _compensation_improvement(job, criteria)
    work = _work_model_improvement(job)
    commute = _commute_improvement(job)
    scope = "strong_p_and_l_path" if _has_pnl_scope(job) else "none"
    values = [comp, work, commute, scope]
    evidence_count = sum(value not in {"unknown", "none"} for value in values)
    negatives = sum(value in {"below_current_total_comp", "below_current_base", "penalty", "worse"} for value in values)
    positives = sum(value in {"target_total_comp", "serious_move_total_comp", "meets_role_base_floor", "strong_improvement", "improvement", "strong_p_and_l_path"} for value in values)
    if evidence_count == 0:
        classification = "insufficient_evidence"
    elif negatives >= 2 and comp not in {"target_total_comp", "serious_move_total_comp"}:
        classification = "worse"
    elif comp in {"target_total_comp", "serious_move_total_comp"} and (positives >= 2 or scope == "strong_p_and_l_path"):
        classification = "clearly_better"
    elif positives >= 2 and negatives == 0:
        classification = "potentially_better"
    elif negatives >= 1 and positives == 0:
        classification = "worse"
    else:
        classification = "lateral_or_uncertain"
    notes = "; ".join([f"compensation={comp}", f"compensation_status={comp_status}", f"work_model={work}", f"commute={commute}", f"scope={scope}"])
    return {
        "compensation_improvement": comp,
        "total_compensation_improvement": comp,
        "work_model_improvement": work,
        "commute_improvement": commute,
        "benefits_confidence_summary": job.benefits_confidence,
        "scope_p_and_l_modifier": scope,
        "move_value_classification": classification,
        "move_value_notes": notes,
        "move_value_updated_at": utc_now_iso(),
    }


def apply_move_value(job: JobPosting, criteria: MoveCriteria | None = None) -> JobPosting:
    values = job.to_dict()
    values.update(calculate_move_value(job, criteria))
    return JobPosting.from_dict(values)


def apply_user_decision_evidence(job: JobPosting, **updates: Any) -> JobPosting:
    values = job.to_dict()
    values.update(updates)
    if updates.get("compensation_source_type") == "user_entered" and not values.get("compensation_observed_date"):
        values["compensation_observed_date"] = today_iso()
    if updates.get("work_model_source") == "user_entered" and not values.get("work_model_confidence"):
        values["work_model_confidence"] = "confirmed"
    return JobPosting.from_dict(values)


def _merge_text(existing_value: str, incoming_value: str) -> str:
    existing = str(existing_value or "").strip()
    incoming = str(incoming_value or "").strip()
    if not existing:
        return incoming
    if not incoming or incoming == existing:
        return existing
    return f"{existing}\n{incoming}"


def _material_conflict(existing: JobPosting, incoming: JobPosting, fields: list[str]) -> bool:
    for field in fields:
        left = getattr(existing, field)
        right = getattr(incoming, field)
        if left not in (None, "") and right not in (None, "") and left != right:
            return True
    return False


def merge_decision_evidence(existing: JobPosting, incoming: JobPosting) -> JobPosting:
    values = existing.to_dict()
    notes = existing.decision_evidence_conflict_notes or incoming.decision_evidence_conflict_notes
    comp_fields = ["base_salary_min", "base_salary_max", "salary_currency", "bonus_target_percent", "bonus_max_percent", "commission_estimate", "equity_or_lti_estimate", "sign_on_bonus", "estimated_total_comp_min", "estimated_total_comp_max", "compensation_source_type", "compensation_source_url", "compensation_observed_date", "compensation_confidence", "compensation_notes"]
    work_fields = ["work_model", "required_office_days_per_week", "travel_percentage", "relocation_required", "geographic_eligibility", "work_model_source", "work_model_confidence", "work_model_notes"]
    other_fields = ["office_name", "office_street_address", "office_city", "office_state", "office_postal_code", "location_confidence", "estimated_one_way_distance", "estimated_one_way_travel_time", "commute_bucket", "commute_calculation_date", "commute_method", "commute_notes", "benefit_401k_match", "health_insurance_indicators", "paid_parental_leave", "pto", "pension", "tuition_reimbursement", "other_material_benefits", "benefits_source", "benefits_confidence", "benefits_notes"]
    if COMPENSATION_SOURCE_RANK.get(incoming.compensation_source_type, 0) > COMPENSATION_SOURCE_RANK.get(existing.compensation_source_type, 0):
        for field in comp_fields:
            values[field] = getattr(incoming, field)
    else:
        for field in comp_fields:
            if values.get(field) in (None, "") and getattr(incoming, field) not in (None, ""):
                values[field] = getattr(incoming, field)
    if _material_conflict(existing, incoming, ["base_salary_min", "base_salary_max", "estimated_total_comp_min", "estimated_total_comp_max"]):
        notes = _merge_text(notes, "conflicting_compensation_evidence")
    if WORK_MODEL_RANK.get(incoming.work_model_source, 0) > WORK_MODEL_RANK.get(existing.work_model_source, 0):
        for field in work_fields:
            values[field] = getattr(incoming, field)
    else:
        for field in work_fields:
            if values.get(field) in (None, "") and getattr(incoming, field) not in (None, ""):
                values[field] = getattr(incoming, field)
    if _material_conflict(existing, incoming, ["work_model", "required_office_days_per_week"]):
        notes = _merge_text(notes, "conflicting_work_model_evidence")
    for field in other_fields:
        if values.get(field) in (None, "") and getattr(incoming, field) not in (None, ""):
            values[field] = getattr(incoming, field)
    values["decision_evidence_conflict_notes"] = notes
    merged = JobPosting.from_dict(values)
    updated = merged.to_dict()
    updated.update(calculate_move_value(merged))
    return JobPosting.from_dict(updated)


def _money(value: Any) -> str:
    amount = _optional_int(value)
    return f"${amount:,}" if amount else ""


def _display_comp(job: JobPosting) -> str:
    low = _money(job.base_salary_min or job.salary_min)
    high = _money(job.base_salary_max or job.salary_max)
    total = _money(_best_total_comp(job))
    if low and high and low != high:
        base = f"{low} to {high} base"
    elif low or high:
        base = f"{low or high} base"
    else:
        base = "Comp unknown"
    return f"{base}; {total} total" if total else base


def _dashboard_row(job: JobPosting, criteria: MoveCriteria) -> list[Any]:
    values = calculate_move_value(job, criteria)
    bucket = job.commute_bucket or calculate_commute_bucket(travel_time_minutes=job.estimated_one_way_travel_time or job.commute_estimate_minutes, distance_miles=job.estimated_one_way_distance)
    return [job.company, job.title, job.location, _visible_score(job), MOVE_VALUE_LABELS[values["move_value_classification"]], _display_comp(job), compensation_status(job.compensation_source_type, has_amount=_best_base(job) is not None or _best_total_comp(job) is not None), job.work_model, job.required_office_days_per_week if job.required_office_days_per_week is not None else "", bucket, job.canonical_url, values["move_value_notes"]]


def _top(jobs: list[JobPosting], criteria: MoveCriteria, limit: int) -> list[list[Any]]:
    selected = sorted(jobs, key=lambda job: (_visible_score(job), job.potential_priority_score), reverse=True)[:limit]
    if not selected:
        return [["No matching roles", "", "", "", "", "", "", "", "", "", "", ""]]
    return [_dashboard_row(job, criteria) for job in selected]


def build_move_value_dashboard_sections(jobs: list[JobPosting], criteria: MoveCriteria | None = None, *, limit: int = 10) -> list[list[Any]]:
    criteria = criteria or MoveCriteria()
    open_jobs = [job for job in jobs if job.status in {"open", "reopened"}]
    scored = [(job, calculate_move_value(job, criteria)) for job in open_jobs]
    strong = [job for job in open_jobs if _visible_score(job) >= 75 or job.potential_priority == "high"]
    has_comp = lambda job: _best_base(job) is not None or _best_total_comp(job) is not None
    confirmed = [job for job in strong if compensation_status(job.compensation_source_type, has_amount=has_comp(job)) == "confirmed"]
    unknown = [job for job in strong if compensation_status(job.compensation_source_type, has_amount=has_comp(job)) == "unknown"]
    remote_hybrid = [job for job in open_jobs if job.work_model in {"remote", "hybrid"}]
    short_commute = [job for job in open_jobs if calculate_commute_bucket(travel_time_minutes=job.estimated_one_way_travel_time or job.commute_estimate_minutes, distance_miles=job.estimated_one_way_distance) in {"under_15_minutes", "15_to_30_minutes"}]
    five_day = [job for job in open_jobs if job.work_model == "on_site" and (job.required_office_days_per_week or 0) >= 5]
    serious = [job for job in open_jobs if (_best_total_comp(job) or 0) >= criteria.serious_move_total_comp]
    comp_follow_up = [job for job in strong if compensation_status(job.compensation_source_type, has_amount=has_comp(job)) != "confirmed"]
    work_follow_up = [job for job in strong if job.work_model == "unknown"]
    counts = {key: sum(1 for _, values in scored if values["move_value_classification"] == key) for key in MOVE_VALUE_LABELS}
    header = ["Company", "Title", "Location", "Score", "Move value", "Compensation", "Comp status", "Work model", "Office days", "Commute bucket", "URL", "Evidence notes"]
    rows: list[list[Any]] = [
        ["Move-value intelligence"],
        ["Classification", "Count", "Meaning"],
        ["Clearly better", counts["clearly_better"], "Compensation, flexibility, commute, or scope clearly improves the current role"],
        ["Potentially better", counts["potentially_better"], "Positive evidence exists, but the move case is not complete"],
        ["Lateral or uncertain", counts["lateral_or_uncertain"], "Evidence is mixed or near current-role economics"],
        ["Worse", counts["worse"], "Evidence indicates weaker economics, flexibility, or commute"],
        ["Insufficient evidence", counts["insufficient_evidence"], "Missing evidence is kept separate from negative evidence"],
    ]
    sections = [
        ("Strong roles with confirmed compensation", confirmed),
        ("Strong roles with unknown compensation", unknown),
        ("Remote or hybrid opportunities", remote_hybrid),
        ("Short-commute opportunities", short_commute),
        ("Five-day on-site penalties", five_day),
        ("Roles meeting serious-move compensation", serious),
        ("Roles requiring compensation follow-up", comp_follow_up),
        ("Roles requiring work-model follow-up", work_follow_up),
    ]
    for title, selected in sections:
        rows.extend([[], [title], header, *_top(selected, criteria, limit)])
    return rows


def move_value_summary(job: JobPosting, criteria: MoveCriteria | None = None) -> dict[str, Any]:
    return asdict(criteria or MoveCriteria()) | calculate_move_value(job, criteria)
