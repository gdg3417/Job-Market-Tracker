from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from src.models import JobPosting

OPEN_STATUSES = {"open", "reopened"}
ACTIVE_ENRICHMENT_STATUSES = {
    "in_progress",
    "partial",
    "enriched",
    "ambiguous",
    "not_found",
    "retryable_failure",
    "permanent_failure",
    "closed",
}
INCOMPLETE_EVIDENCE_VALUES = {"", "unknown", "unspecified", "not specified", "n/a", "na", "none"}


def load_potential_priority_rules(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", _as_text(value)).strip().lower()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized = _normalize(phrase)
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", normalized):
        return re.search(r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])", text) is not None
    return normalized in text


def _best_signal(text: str, signals: dict[str, Any] | None) -> tuple[int, str]:
    best_points = 0
    best_signal = ""
    for signal, points_value in (signals or {}).items():
        try:
            points = int(points_value)
        except (TypeError, ValueError):
            continue
        if _contains_phrase(text, str(signal)) and points >= best_points:
            best_points = points
            best_signal = str(signal)
    return best_points, best_signal


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _credible_url(value: str) -> bool:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _is_generic_description(description: str, rules: dict[str, Any]) -> bool:
    text = _normalize(description)
    if not text:
        return True
    prefixes = rules.get("generic_description_prefixes") or ["Extracted from Gmail job alert"]
    return any(_normalize(prefix) in text for prefix in prefixes if _normalize(prefix))


def _description_word_count(description: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9&'/-]*", str(description or "")))


def _target_company_points(company_context: dict[str, Any] | None, rules: dict[str, Any]) -> tuple[int, str]:
    if not company_context:
        return 0, ""
    target_rules = rules.get("target_company", {}) or {}
    points = _safe_int(target_rules.get("points"), 5)
    context_text = _normalize(company_context)
    for term in target_rules.get("priority_terms") or []:
        if _contains_phrase(context_text, str(term)):
            return points, str(term)
    if _safe_int(company_context.get("score_boost_points"), 0) > 0:
        return points, "score boost"
    if str(company_context.get("target_company") or "").strip().lower() in {"1", "true", "yes", "y"}:
        return points, "target company"
    return 0, ""


def evaluate_potential_priority(
    job: JobPosting,
    rules: dict[str, Any],
    *,
    company_context: dict[str, Any] | None = None,
    hard_exclude: bool = False,
) -> tuple[int, str, str]:
    if hard_exclude or job.alert_tier == "exclude" or "hard_exclude=true" in str(job.score_explanation or "").lower():
        return 0, "excluded", "hard exclusion"

    title_text = _normalize(f"{job.title} {job.role_level}")
    role_text = _normalize(f"{job.title} {job.role_family}")
    ownership_text = _normalize(f"{job.title} {job.role_family}")
    company_text = _normalize(job.company)
    context_text = _normalize(company_context or {})
    location_text = _normalize(f"{job.location} {job.remote_status} {job.work_model}")

    seniority_score, seniority_signal = _best_signal(title_text, rules.get("seniority_signals"))
    role_score, role_signal = _best_signal(role_text, rules.get("role_family_signals"))
    ownership_score, ownership_signal = _best_signal(ownership_text, rules.get("strategic_ownership_signals"))
    company_name_score, company_name_signal = _best_signal(company_text, rules.get("company_name_signals"))
    context_score, context_signal = _best_signal(context_text, rules.get("company_context_signals"))
    company_score = max(company_name_score, context_score)
    company_signal = company_name_signal if company_name_score >= context_score else context_signal
    if company_score == 0 and str(job.company or "").strip():
        company_score = _safe_int(rules.get("credible_company_default"), 5)
        company_signal = "credible company"
    location_score, location_signal = _best_signal(location_text, rules.get("location_signals"))
    if location_score == 0 and _normalize(job.location) not in INCOMPLETE_EVIDENCE_VALUES:
        location_score = _safe_int(rules.get("location_default"), 3)
        location_signal = "known location"
    target_score, target_signal = _target_company_points(company_context, rules)

    score_scale = _safe_int(rules.get("score_scale"), 100)
    total = min(
        score_scale,
        seniority_score + role_score + ownership_score + company_score + location_score + target_score,
    )
    thresholds = rules.get("thresholds", {}) or {}
    high_threshold = _safe_int(thresholds.get("high"), 70)
    medium_threshold = _safe_int(thresholds.get("medium"), 50)
    priority = "high" if total >= high_threshold else "medium" if total >= medium_threshold else "low"

    reason_parts = [
        f"seniority={seniority_score}" + (f" ({seniority_signal})" if seniority_signal else ""),
        f"role_family={role_score}" + (f" ({role_signal})" if role_signal else ""),
        f"ownership={ownership_score}" + (f" ({ownership_signal})" if ownership_signal else ""),
        f"company={company_score}" + (f" ({company_signal})" if company_signal else ""),
        f"location={location_score}" + (f" ({location_signal})" if location_signal else ""),
        f"target_company={target_score}" + (f" ({target_signal})" if target_signal else ""),
    ]
    return total, priority, "; ".join(reason_parts)


def calculate_evidence_completeness(
    job: JobPosting,
    rules: dict[str, Any],
    *,
    company_context: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    weights = rules.get("evidence_weights", {}) or {}
    evidence_rules = rules.get("evidence_rules", {}) or {}
    description = str(job.description_text or "")
    description_text = _normalize(description)
    word_count = _description_word_count(description)
    generic_description = _is_generic_description(description, evidence_rules)
    meaningful_min = _safe_int(evidence_rules.get("meaningful_description_min_words"), 20)
    partial_min = _safe_int(evidence_rules.get("partial_description_min_words"), 8)

    total = 0
    evidence: list[str] = []
    description_weight = _safe_int(weights.get("full_description"), 30)
    if not generic_description and word_count >= meaningful_min:
        total += description_weight
        evidence.append("full description")
    elif not generic_description and word_count >= partial_min:
        total += max(1, description_weight // 2)
        evidence.append("partial description")

    if not generic_description and any(
        _contains_phrase(description_text, str(term)) for term in evidence_rules.get("responsibility_terms") or []
    ):
        total += _safe_int(weights.get("responsibilities"), 15)
        evidence.append("responsibilities")

    if not generic_description and any(
        _contains_phrase(description_text, str(term)) for term in evidence_rules.get("qualification_terms") or []
    ):
        total += _safe_int(weights.get("qualifications"), 10)
        evidence.append("qualifications")

    if company_context:
        total += _safe_int(weights.get("company_context"), 10)
        evidence.append("company context")

    if _normalize(job.remote_status) not in INCOMPLETE_EVIDENCE_VALUES and _normalize(job.work_model) not in INCOMPLETE_EVIDENCE_VALUES:
        total += _safe_int(weights.get("work_model"), 10)
        evidence.append("work model")

    if job.salary_min is not None or job.salary_max is not None or job.total_comp_estimate is not None:
        total += _safe_int(weights.get("compensation"), 10)
        evidence.append("compensation")

    if _normalize(job.location) not in INCOMPLETE_EVIDENCE_VALUES:
        total += _safe_int(weights.get("location"), 5)
        evidence.append("location")

    if _credible_url(job.canonical_url) and str(job.first_seen_date or job.last_seen_date or "").strip():
        total += _safe_int(weights.get("posting_date_status"), 5)
        evidence.append("posting date and status")

    if not generic_description and any(
        _contains_phrase(description_text, str(term)) for term in evidence_rules.get("team_reporting_terms") or []
    ):
        total += _safe_int(weights.get("team_reporting_structure"), 5)
        evidence.append("team or reporting structure")

    return min(_safe_int(rules.get("score_scale"), 100), total), evidence


def _score_status(
    job: JobPosting,
    rules: dict[str, Any],
    *,
    evidence_score: int,
    hard_exclude: bool,
) -> str:
    if hard_exclude or job.alert_tier == "exclude":
        return "excluded"
    enrichment_rules = rules.get("enrichment", {}) or {}
    complete_threshold = _safe_int(enrichment_rules.get("complete_evidence_threshold"), 70)
    partial_threshold = _safe_int(enrichment_rules.get("partial_evidence_threshold"), 40)
    evidence_rules = rules.get("evidence_rules", {}) or {}
    meaningful_description = not _is_generic_description(job.description_text, evidence_rules) and _description_word_count(
        job.description_text
    ) >= _safe_int(evidence_rules.get("meaningful_description_min_words"), 20)
    credible_identity = bool(str(job.title or "").strip() and str(job.company or "").strip() and _credible_url(job.canonical_url))
    credible_location = bool(
        _normalize(job.location) not in INCOMPLETE_EVIDENCE_VALUES
        or _normalize(job.remote_status) not in INCOMPLETE_EVIDENCE_VALUES
        or _normalize(job.work_model) not in INCOMPLETE_EVIDENCE_VALUES
    )
    if evidence_score >= complete_threshold and meaningful_description and credible_identity and credible_location:
        return "verified"
    if evidence_score >= partial_threshold:
        return "partially_verified"
    return "provisional"


def _strip_model_tags(explanation: str) -> str:
    prefixes = (
        "potential_priority=",
        "potential_priority_score=",
        "evidence_completeness=",
        "score_status=",
        "enrichment_status=",
    )
    parts = [part.strip() for part in str(explanation or "").split(";") if part.strip()]
    return "; ".join(part for part in parts if not part.startswith(prefixes))


def apply_potential_priority(
    job: JobPosting,
    rules: dict[str, Any],
    *,
    company_context: dict[str, Any] | None = None,
    hard_exclude: bool = False,
) -> JobPosting:
    score, priority, reason = evaluate_potential_priority(
        job,
        rules,
        company_context=company_context,
        hard_exclude=hard_exclude,
    )
    evidence_score, _ = calculate_evidence_completeness(job, rules, company_context=company_context)
    score_status = _score_status(job, rules, evidence_score=evidence_score, hard_exclude=hard_exclude)

    job.potential_priority_score = score
    job.potential_priority = priority
    job.potential_priority_reason = reason
    job.evidence_completeness_score = evidence_score
    job.score_status = score_status

    if score_status == "verified":
        job.verified_total_score = job.total_score
        job.verified_alert_tier = job.alert_tier
    elif score_status == "excluded":
        job.verified_total_score = 0
        job.verified_alert_tier = "exclude"
    else:
        job.verified_total_score = None
        job.verified_alert_tier = ""

    enrichment_rules = rules.get("enrichment", {}) or {}
    include_medium = bool(enrichment_rules.get("include_medium_when_capacity", False))
    priority_is_eligible = priority == "high" or (include_medium and priority == "medium")
    eligible = (
        score_status in {"provisional", "partially_verified"}
        and priority_is_eligible
        and job.status in OPEN_STATUSES
        and bool(str(job.title or "").strip() and str(job.company or "").strip())
        and _credible_url(job.canonical_url)
    )
    current_status = str(job.enrichment_status or "").strip().lower()
    if eligible:
        if current_status not in ACTIVE_ENRICHMENT_STATUSES:
            job.enrichment_status = "pending"
        job.enrichment_priority = priority
    else:
        if current_status not in ACTIVE_ENRICHMENT_STATUSES or score_status in {"verified", "excluded"}:
            job.enrichment_status = "not_required"
        if job.enrichment_status == "not_required":
            job.enrichment_priority = ""

    base_explanation = _strip_model_tags(job.score_explanation)
    model_tags = "; ".join(
        [
            f"potential_priority={priority}",
            f"potential_priority_score={score}",
            f"evidence_completeness={evidence_score}",
            f"score_status={score_status}",
            f"enrichment_status={job.enrichment_status}",
        ]
    )
    job.score_explanation = "; ".join(part for part in [base_explanation, model_tags] if part)
    return job
