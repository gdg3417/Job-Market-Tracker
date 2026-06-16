from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.models import JobPosting

ScoreMatch = tuple[int, list[str]]


def load_scoring_rules(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        rules = yaml.safe_load(file) or {}
    rules.setdefault("category_weights", {})
    rules.setdefault("alert_thresholds", {})
    rules.setdefault("positive_keywords", {})
    rules.setdefault("negative_keywords", {})
    return rules


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _text_for_job(job: JobPosting, company_context: dict[str, Any] | None = None) -> str:
    parts = [
        job.title,
        job.company,
        job.location,
        job.remote_status,
        job.work_model,
        job.role_family,
        job.role_level,
        job.description_text,
        _as_text(company_context or {}),
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).strip().lower()


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase_text = phrase.strip().lower()
    if not phrase_text:
        return False
    if re.match(r"^[a-z0-9 ]+$", phrase_text):
        pattern = r"(?<![a-z0-9])" + re.escape(phrase_text) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
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


def _weighted_keyword_score(text: str, keywords: list[str], max_points: int, target_matches: int) -> ScoreMatch:
    matches = _matching_keywords(text, keywords)
    if not matches or max_points <= 0:
        return 0, []
    target = max(1, target_matches)
    score = round(max_points * min(1, len(matches) / target))
    return min(max_points, score), matches


def _score_fit(job: JobPosting, text: str, rules: dict[str, Any]) -> tuple[int, list[str]]:
    weights = rules.get("category_weights", {})
    max_points = int(weights.get("fit_score", weights.get("role_level_score", 15)))
    role_level_score = 0
    role_level_match = ""
    title_and_level = f"{job.title} {job.role_level}".lower()
    for keyword, points in rules.get("role_level_keywords", {}).items():
        if _contains_phrase(title_and_level, str(keyword)):
            if int(points) > role_level_score:
                role_level_score = int(points)
                role_level_match = str(keyword)
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
    evidence = []
    if role_level_match:
        evidence.append(role_level_match)
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


def _score_industry(company_context: dict[str, Any] | None, rules: dict[str, Any]) -> tuple[int, list[str]]:
    if not company_context:
        return 0, []
    text = _as_text(company_context).lower()
    matches: list[str] = []
    score = 0
    for keyword, points in rules.get("industry_fit", {}).items():
        if _contains_phrase(text, str(keyword)):
            matches.append(str(keyword))
            score = max(score, int(points))
    for keyword in rules.get("industry_exclusions", []) or []:
        if _contains_phrase(text, str(keyword)):
            matches.append(f"excluded industry: {keyword}")
            score = 0
            break
    return score, matches


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


def score_job(job: JobPosting, rules: dict[str, Any], company_context: dict[str, Any] | None = None) -> JobPosting:
    text = _text_for_job(job, company_context)
    weights = rules.get("category_weights", {})
    targets = rules.get("category_match_targets", {})
    positive = rules.get("positive_keywords", {})

    fit_score, fit_evidence = _score_fit(job, text, rules)
    p_and_l_score, p_and_l_matches = _weighted_keyword_score(
        text,
        list(positive.get("p_and_l_path", []) or []),
        int(weights.get("p_and_l_path_score", 20)),
        int(targets.get("p_and_l_path_score", 3)),
    )
    growth_score, growth_matches = _weighted_keyword_score(
        text,
        list(positive.get("growth_ownership", []) or []),
        int(weights.get("growth_ownership_score", 20)),
        int(targets.get("growth_ownership_score", 3)),
    )
    executive_score, executive_matches = _weighted_keyword_score(
        text,
        list(positive.get("executive_exposure", []) or []),
        int(weights.get("executive_exposure_score", 15)),
        int(targets.get("executive_exposure_score", 2)),
    )
    cadence_score, cadence_matches = _weighted_keyword_score(
        text,
        list(positive.get("operating_cadence", []) or []),
        int(weights.get("operating_cadence_score", 10)),
        int(targets.get("operating_cadence_score", 2)),
    )
    comp_score, comp_evidence = _score_comp(job, rules)
    location_score, location_evidence = _score_location(job, rules)
    industry_score, industry_matches = _score_industry(company_context, rules)
    penalty, penalty_matches, hard_exclude = _negative_penalty(text, rules)

    raw_total = sum(
        [
            fit_score,
            p_and_l_score,
            growth_score,
            executive_score,
            cadence_score,
            comp_score,
            location_score,
            industry_score,
        ]
    )
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
    ]
    if penalty_matches:
        explanation_parts.append(f"penalty={penalty} ({', '.join(penalty_matches[:5])})")
    if hard_exclude:
        explanation_parts.append("hard_exclude=true")

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
    job.refresh_updated_at()
    return job
