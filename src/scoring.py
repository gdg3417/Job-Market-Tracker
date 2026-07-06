from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.company_exclusions import evaluate_company_exclusion, load_company_exclusions
from src.models import JobPosting
from src.potential_priority import apply_potential_priority, load_potential_priority_rules
from src.seniority import SeniorityEvaluation, evaluate_seniority_fit
from src.verified_scoring import finalize_verified_scoring

ScoreMatch = tuple[int, list[str]]
DEFAULT_SPARSE_GMAIL_REVIEW_REASON = "sparse_gmail_high_signal_title"
DEFAULT_GENERIC_GMAIL_DESCRIPTION_PREFIXES = ["Extracted from Gmail job alert"]
DEFAULT_INCOMPLETE_WORK_MODEL_VALUES = {"", "unknown", "unspecified", "not specified", "n/a", "na", "none"}
COMPANY_CONTEXT_SCORING_FIELDS = ("industry_bucket", "ownership_type", "company_size_bucket")
DEFAULT_SENIORITY_FIT_SCORES = {
    "target": 15,
    "stretch": 12,
    "context_dependent": 12,
    "manual_review": 8,
    "too_senior": 0,
    "too_junior": 1,
    "excluded": 0,
    "unknown": 0,
}
SENIORITY_MODEL_TAG_PREFIXES = (
    "potential_priority=",
    "potential_priority_score=",
    "seniority_fit=",
    "seniority_reason=",
    "seniority_penalty=",
)


def load_scoring_rules(path: str | Path) -> dict[str, Any]:
    rules_path = Path(path)
    with rules_path.open("r", encoding="utf-8") as file:
        rules = yaml.safe_load(file) or {}
    rules.setdefault("category_weights", {})
    rules.setdefault("alert_thresholds", {})
    rules.setdefault("positive_keywords", {})
    rules.setdefault("negative_keywords", {})
    rules.setdefault("sparse_gmail_review", {})
    rules.setdefault("seniority_fit_scores", dict(DEFAULT_SENIORITY_FIT_SCORES))
    sparse_rules_path = rules_path.with_name("sparse_gmail_review.yml")
    if sparse_rules_path.exists():
        with sparse_rules_path.open("r", encoding="utf-8") as file:
            sparse_config = yaml.safe_load(file) or {}
        sparse_values = sparse_config.get("sparse_gmail_review", sparse_config)
        if isinstance(sparse_values, dict):
            rules["sparse_gmail_review"].update(sparse_values)
    potential_rules_path = rules_path.with_name("potential_priority_rules.yml")
    rules["potential_priority"] = load_potential_priority_rules(potential_rules_path) if potential_rules_path.exists() else {}
    company_exclusions_path = rules_path.with_name("company_exclusions.yml")
    rules["company_exclusions"] = load_company_exclusions(company_exclusions_path) if company_exclusions_path.exists() else {"blocked_companies": []}
    rules["verified_scoring"] = dict((rules["potential_priority"] or {}).get("verified_scoring") or {})
    return rules


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _text_for_job(job: JobPosting) -> str:
    return re.sub(r"\s+", " ", " ".join([job.title, job.role_family, job.role_level, job.description_text])).strip().lower()


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase_text = str(phrase or "").strip().lower()
    if not phrase_text:
        return False
    if re.match(r"^[a-z0-9 ]+$", phrase_text):
        return re.search(r"(?<![a-z0-9])" + re.escape(phrase_text) + r"(?![a-z0-9])", text) is not None
    return phrase_text in text


def _matching_keywords(text: str, keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    matches: list[str] = []
    for keyword in keywords:
        key = str(keyword).strip()
        normalized = key.lower()
        if normalized and normalized not in seen and _contains_phrase(text, key):
            seen.add(normalized)
            matches.append(key)
    return matches


def _gmail_description_is_generic(description: str, review_rules: dict[str, Any]) -> bool:
    normalized = re.sub(r"\s+", " ", str(description or "")).strip().lower()
    if not normalized:
        return True
    prefixes = list(review_rules.get("generic_description_prefixes") or DEFAULT_GENERIC_GMAIL_DESCRIPTION_PREFIXES)
    matched_prefix = False
    remainder = normalized
    for prefix in prefixes:
        prefix_text = re.sub(r"\s+", " ", str(prefix or "")).strip().lower().rstrip(".")
        if prefix_text and prefix_text in remainder:
            matched_prefix = True
            remainder = remainder.replace(prefix_text, " ")
    if not matched_prefix:
        return False
    remainder = re.sub(
        r"\b(?:confidence|origin|extraction|linkedin_job_id|job_id)\s*=\s*[^;,.\s]+[;,.]?",
        " ",
        remainder,
        flags=re.IGNORECASE,
    )
    remainder = re.sub(r"[^a-z0-9]+", " ", remainder).strip()
    return not remainder


def _work_model_is_incomplete(job: JobPosting, review_rules: dict[str, Any]) -> bool:
    values = {str(value).strip().lower() for value in (review_rules.get("incomplete_work_model_values") or DEFAULT_INCOMPLETE_WORK_MODEL_VALUES)}
    return str(job.remote_status or "").strip().lower() in values or str(job.work_model or "").strip().lower() in values


def is_sparse_gmail_record(job: JobPosting, rules: dict[str, Any] | None = None) -> bool:
    review_rules = (rules or {}).get("sparse_gmail_review", {}) or {}
    if str(job.source_primary or "").strip().lower() != "gmail_alert":
        return False
    if not _gmail_description_is_generic(job.description_text, review_rules):
        return False
    if job.salary_min is not None or job.salary_max is not None or job.total_comp_estimate is not None:
        return False
    return _work_model_is_incomplete(job, review_rules)


def sparse_gmail_review_reason(job: JobPosting, rules: dict[str, Any]) -> str:
    review_rules = rules.get("sparse_gmail_review", {}) or {}
    if not is_sparse_gmail_record(job, rules):
        return ""
    title_text = str(job.title or "").strip().lower()
    priority_matches = _matching_keywords(title_text, list(review_rules.get("priority_title_phrases") or []))
    seniority_matches = _matching_keywords(title_text, list(review_rules.get("seniority_phrases") or []))
    if not priority_matches or not seniority_matches:
        return ""
    return str(review_rules.get("review_reason") or DEFAULT_SPARSE_GMAIL_REVIEW_REASON)


def _weighted_keyword_score(text: str, keywords: list[str], max_points: int, target_matches: int) -> ScoreMatch:
    matches = _matching_keywords(text, keywords)
    if not matches or max_points <= 0:
        return 0, []
    target = max(1, target_matches)
    score = round(max_points * min(1, len(matches) / target))
    return min(max_points, score), matches


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _score_fit(job: JobPosting, text: str, rules: dict[str, Any], seniority: SeniorityEvaluation) -> tuple[int, list[str]]:
    weights = rules.get("category_weights", {})
    max_points = int(weights.get("fit_score", weights.get("role_level_score", 15)))
    configured_scores = dict(DEFAULT_SENIORITY_FIT_SCORES)
    configured_scores.update(rules.get("seniority_fit_scores", {}) or {})
    role_level_score = min(max_points, _safe_int(configured_scores.get(seniority.seniority_fit), 0))
    family_score = int(rules.get("role_family_fit", {}).get(job.role_family, 0))
    family_matches: list[str] = []
    for family, keywords in rules.get("role_family_keywords", {}).items():
        matches = _matching_keywords(text, list(keywords or []))
        if matches:
            family_matches.extend(matches[:3])
            family_score = max(family_score, int(rules.get("role_family_fit", {}).get(family, 0)))
    leadership_matches = _matching_keywords(text, list(rules.get("team_leadership_keywords", []) or []))
    leadership_score = min(2, len(leadership_matches))
    score = min(max_points, role_level_score + family_score + leadership_score)
    evidence = [seniority.reason_code]
    if job.role_family and job.role_family != "Unknown":
        evidence.append(job.role_family)
    evidence.extend(family_matches[:4])
    evidence.extend(leadership_matches[:2])
    return score, evidence


def _score_comp(job: JobPosting, rules: dict[str, Any]) -> tuple[int, str]:
    comp_rules = rules.get("compensation", {})
    total_comp = job.total_comp_estimate or job.salary_max or job.salary_min
    if total_comp is None:
        return 0, "missing salary"
    if total_comp >= int(comp_rules.get("stretch_total_comp", 250000)):
        return 10, f"comp >= {comp_rules.get('stretch_total_comp', 250000)}"
    if total_comp >= int(comp_rules.get("strong_total_comp", 200000)):
        return 9, f"comp >= {comp_rules.get('strong_total_comp', 200000)}"
    if total_comp >= int(comp_rules.get("serious_total_comp", 180000)):
        return 7, f"comp >= {comp_rules.get('serious_total_comp', 180000)}"
    if total_comp >= int(comp_rules.get("director_preferred_floor", 170000)):
        return 5, f"comp >= {comp_rules.get('director_preferred_floor', 170000)}"
    if total_comp >= int(comp_rules.get("base_floor", 140000)):
        return 3, f"comp >= {comp_rules.get('base_floor', 140000)}"
    return 0, f"comp below {comp_rules.get('base_floor', 140000)}"


def _score_location(job: JobPosting, rules: dict[str, Any]) -> tuple[int, str]:
    location_rules = rules.get("location_scoring", {})
    text = f"{job.location} {job.remote_status} {job.work_model} {job.description_text}".lower()
    if _contains_phrase(text, "remote") or job.remote_status == "remote" or job.work_model == "remote":
        return int(location_rules.get("remote", 5)), "remote"
    if _contains_phrase(text, "hybrid") or job.remote_status == "hybrid" or job.work_model == "hybrid":
        if _contains_phrase(text, "4 days in office") or _contains_phrase(text, "four days in office"):
            return int(location_rules.get("hybrid_4_days", 3)), "hybrid 4 days"
        return int(location_rules.get("hybrid_2_to_3_days", 5)), "hybrid"
    if job.commute_estimate_minutes is not None:
        minutes = int(job.commute_estimate_minutes)
        if minutes < 15:
            return int(location_rules.get("onsite_under_15_minutes", 5)), "commute under 15 minutes"
        if minutes <= 30:
            return int(location_rules.get("onsite_15_to_30_minutes", 4)), "commute 15 to 30 minutes"
        if minutes <= 45:
            return int(location_rules.get("onsite_30_to_45_minutes", 2)), "commute 30 to 45 minutes"
        return int(location_rules.get("onsite_over_45_minutes", 0)), "commute over 45 minutes"
    for location, points in location_rules.items():
        if str(location).startswith("onsite_") or location in {"remote", "hybrid_2_to_3_days", "hybrid_4_days", "default"}:
            continue
        if _contains_phrase(text, str(location)):
            return int(points), str(location)
    return int(location_rules.get("default", 1)), "default location"


def _safe_context_boost(company_context: dict[str, Any] | None, cap: int) -> tuple[int, str]:
    if not company_context or cap <= 0:
        return 0, ""
    raw = company_context.get("score_boost_points", company_context.get("company_preference_boost", 0))
    try:
        requested = max(0, int(float(str(raw or 0).strip())))
    except (TypeError, ValueError):
        requested = 0
    if requested <= 0:
        return 0, ""
    applied = min(cap, requested)
    return applied, f"target company boost {applied} (requested {requested}, capped {cap})"


def _score_company_context(company_context: dict[str, Any] | None, rules: dict[str, Any]) -> tuple[int, list[str]]:
    max_points = int((rules.get("category_weights", {}) or {}).get("industry_match_score", 5))
    if not company_context:
        return 0, []
    text = _as_text([company_context.get(field_name) for field_name in COMPANY_CONTEXT_SCORING_FIELDS]).lower()
    matches: list[str] = []
    industry_score = 0
    for keyword, points in rules.get("industry_fit", {}).items():
        if _contains_phrase(text, str(keyword)):
            matches.append(str(keyword))
            industry_score = max(industry_score, int(points))
    for keyword in rules.get("industry_exclusions", []) or []:
        if _contains_phrase(text, str(keyword)):
            matches.append(f"excluded industry: {keyword}")
            return 0, matches
    boost, boost_reason = _safe_context_boost(company_context, max_points)
    if boost_reason:
        matches.append(boost_reason)
    return min(max_points, max(industry_score, boost)), matches


def _negative_penalty(text: str, rules: dict[str, Any]) -> tuple[int, list[str], bool]:
    negative = rules.get("negative_keywords", {})
    hard_matches = _matching_keywords(text, list(negative.get("hard_exclude", []) or []))
    if hard_matches:
        return 100, hard_matches, True
    matches: list[str] = []
    total = 0
    penalties = negative.get("penalties", {}) or {}
    for keyword, penalty in penalties.items():
        if _contains_phrase(text, str(keyword)):
            matches.append(str(keyword))
            total += int(penalty)
    return total, matches, False


def _alert_tier(total_score: int, hard_exclude: bool, rules: dict[str, Any]) -> str:
    if hard_exclude:
        return str(rules.get("alert_tiers", {}).get("hard_exclude", "exclude"))
    thresholds = rules.get("alert_thresholds", {})
    if total_score >= int(thresholds.get("immediate_review", 85)):
        return "immediate_review"
    if total_score >= int(thresholds.get("strong_fit", 75)):
        return "strong_fit"
    if total_score >= int(thresholds.get("track_only", 65)):
        return "track_only"
    return str(rules.get("alert_tiers", {}).get("below_track", "ignore"))


def _explain(label: str, score: int, evidence: list[str] | str | None = None) -> str:
    if isinstance(evidence, str):
        evidence_text = evidence
    else:
        evidence_text = ", ".join((evidence or [])[:5])
    if evidence_text:
        return f"{label}={score} ({evidence_text})"
    return f"{label}={score}"


def _replace_model_tags(explanation: str, replacement_tags: list[str]) -> str:
    parts = [part.strip() for part in str(explanation or "").split(";") if part.strip()]
    retained = [part for part in parts if not part.startswith(SENIORITY_MODEL_TAG_PREFIXES)]
    return "; ".join([*retained, *replacement_tags])


def _apply_seniority_priority_override(job: JobPosting, seniority: SeniorityEvaluation) -> None:
    if seniority.hard_exclude:
        job.potential_priority_score = 0
        job.potential_priority = "excluded"
        job.potential_priority_reason = f"hard exclusion; seniority={seniority.reason_code}"
        job.enrichment_status = "not_required"
        job.enrichment_priority = ""
    elif seniority.seniority_fit == "too_senior":
        job.potential_priority_score = min(int(job.potential_priority_score or 0), 20)
        job.potential_priority = "low"
        reason = f"seniority={seniority.reason_code}; too senior for normal viable queue"
        job.potential_priority_reason = "; ".join(part for part in [reason, job.potential_priority_reason] if part)
        if job.enrichment_status in {"", "not_required", "pending", "closed"}:
            job.enrichment_status = "not_required"
            job.enrichment_priority = ""
    else:
        return
    job.score_explanation = _replace_model_tags(
        job.score_explanation,
        [
            f"potential_priority={job.potential_priority}",
            f"potential_priority_score={job.potential_priority_score}",
            f"seniority_fit={seniority.seniority_fit}",
            f"seniority_reason={seniority.reason_code}",
            f"seniority_penalty={seniority.score_penalty}",
        ],
    )


def _apply_company_exclusion(
    job: JobPosting,
    rules: dict[str, Any],
    company_context: dict[str, Any] | None,
    *,
    canonical_name: str,
    matched_alias: str,
    reason_code: str,
    category: str,
) -> JobPosting:
    alert_tier = str(rules.get("alert_tiers", {}).get("hard_exclude", "exclude"))
    job.fit_score = 0
    job.p_and_l_path_score = 0
    job.growth_ownership_score = 0
    job.executive_exposure_score = 0
    job.operating_cadence_score = 0
    job.comp_score = 0
    job.location_score = 0
    job.industry_match_score = 0
    job.total_score = 0
    job.alert_tier = alert_tier
    job.score_explanation = "; ".join(
        [
            "total=0",
            f"tier={alert_tier}",
            "fit=0",
            "p_and_l=0",
            "growth=0",
            "executive=0",
            "cadence=0",
            "comp=0",
            "location=0",
            "industry=0",
            "company_exclusion=true",
            f"company_exclusion_reason={reason_code}",
            f"company_exclusion_category={category}",
            f"company_exclusion_match={canonical_name}",
            f"company_exclusion_alias={matched_alias}",
            "hard_exclude=true",
        ]
    )
    apply_potential_priority(job, rules.get("potential_priority", {}) or {}, company_context=company_context, hard_exclude=True)
    finalize_verified_scoring(job, rules, company_context=company_context, hard_exclude=True)
    job.refresh_updated_at()
    return job


def score_job(job: JobPosting, rules: dict[str, Any], company_context: dict[str, Any] | None = None) -> JobPosting:
    company_exclusion = evaluate_company_exclusion(job.company, rules.get("company_exclusions", {}) or {})
    if company_exclusion.blocked:
        return _apply_company_exclusion(
            job,
            rules,
            company_context,
            canonical_name=company_exclusion.canonical_name,
            matched_alias=company_exclusion.matched_alias,
            reason_code=company_exclusion.reason_code,
            category=company_exclusion.category,
        )

    seniority = evaluate_seniority_fit(job.title, job.role_level, company_context)
    job.role_level = seniority.normalized_level
    job_text = _text_for_job(job)
    weights = rules.get("category_weights", {})
    targets = rules.get("category_match_targets", {})
    positive = rules.get("positive_keywords", {})

    fit_score, fit_evidence = _score_fit(job, job_text, rules, seniority)
    p_and_l_score, p_and_l_matches = _weighted_keyword_score(
        job_text,
        list(positive.get("p_and_l_path", []) or []),
        int(weights.get("p_and_l_path_score", 20)),
        int(targets.get("p_and_l_path_score", 3)),
    )
    growth_score, growth_matches = _weighted_keyword_score(
        job_text,
        list(positive.get("growth_ownership", []) or []),
        int(weights.get("growth_ownership_score", 20)),
        int(targets.get("growth_ownership_score", 3)),
    )
    executive_score, executive_matches = _weighted_keyword_score(
        job_text,
        list(positive.get("executive_exposure", []) or []),
        int(weights.get("executive_exposure_score", 15)),
        int(targets.get("executive_exposure_score", 2)),
    )
    cadence_score, cadence_matches = _weighted_keyword_score(
        job_text,
        list(positive.get("operating_cadence", []) or []),
        int(weights.get("operating_cadence_score", 10)),
        int(targets.get("operating_cadence_score", 2)),
    )
    comp_score, comp_evidence = _score_comp(job, rules)
    location_score, location_evidence = _score_location(job, rules)
    industry_score, industry_matches = _score_company_context(company_context, rules)
    penalty, penalty_matches, hard_exclude = _negative_penalty(job_text, rules)
    if seniority.score_penalty:
        penalty += seniority.score_penalty
        penalty_matches.append(seniority.reason_code)
    if seniority.hard_exclude:
        hard_exclude = True
    raw_total = sum([fit_score, p_and_l_score, growth_score, executive_score, cadence_score, comp_score, location_score, industry_score])
    total = 0 if hard_exclude else max(0, min(int(rules.get("score_scale", 100)), raw_total - penalty))
    alert_tier = _alert_tier(total, hard_exclude, rules)
    explanation_parts = [
        f"total={total}",
        f"tier={alert_tier}",
        _explain("fit", fit_score, fit_evidence),
        _explain("p_and_l", p_and_l_score, p_and_l_matches),
        _explain("growth", growth_score, growth_matches),
        _explain("executive", executive_score, executive_matches),
        _explain("cadence", cadence_score, cadence_matches),
        _explain("comp", comp_score, comp_evidence),
        _explain("location", location_score, location_evidence),
        _explain("industry", industry_score, industry_matches),
        f"seniority_fit={seniority.seniority_fit}",
        f"seniority_reason={seniority.reason_code}",
        f"seniority_penalty={seniority.score_penalty}",
    ]
    if penalty_matches:
        explanation_parts.append(f"penalty={penalty} ({', '.join(penalty_matches[:5])})")
    if hard_exclude:
        explanation_parts.append("hard_exclude=true")
    else:
        review_reason = sparse_gmail_review_reason(job, rules)
        if seniority.manual_review:
            review_reason = seniority.reason_code
        if review_reason:
            explanation_parts.extend(["manual_review=true", f"review_reason={review_reason}"])
    job.fit_score = fit_score
    job.p_and_l_path_score = p_and_l_score
    job.growth_ownership_score = growth_score
    job.executive_exposure_score = executive_score
    job.operating_cadence_score = cadence_score
    job.comp_score = comp_score
    job.location_score = location_score
    job.industry_match_score = industry_score
    job.total_score = total
    job.alert_tier = alert_tier
    job.score_explanation = "; ".join(explanation_parts)
    apply_potential_priority(job, rules.get("potential_priority", {}) or {}, company_context=company_context, hard_exclude=hard_exclude)
    _apply_seniority_priority_override(job, seniority)
    finalize_verified_scoring(job, rules, company_context=company_context, hard_exclude=hard_exclude)
    job.refresh_updated_at()
    return job
