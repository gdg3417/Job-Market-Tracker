from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.models import JobPosting


def load_scoring_rules(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _text_for_job(job: JobPosting) -> str:
    return f"{job.title} {job.company} {job.location} {job.description_text}".lower()


def _keyword_score(text: str, keywords: list[str], max_points: int) -> tuple[int, list[str]]:
    matches = [keyword for keyword in keywords if keyword.lower() in text]
    if not matches:
        return 0, []
    per_match = max(1, max_points // 2)
    return min(max_points, per_match * len(matches)), matches


def _score_comp(job: JobPosting, rules: dict[str, Any]) -> int:
    comp_rules = rules.get("compensation", {})
    total_comp = job.total_comp_estimate or job.salary_max or job.salary_min
    if total_comp is None:
        return 0
    if total_comp >= comp_rules.get("stretch_total_comp", 250000):
        return 10
    if total_comp >= comp_rules.get("strong_total_comp", 200000):
        return 8
    if total_comp >= comp_rules.get("serious_total_comp", 180000):
        return 6
    if total_comp >= comp_rules.get("base_floor", 140000):
        return 3
    return 0


def _score_location(job: JobPosting, rules: dict[str, Any]) -> int:
    location_rules = rules.get("location_scoring", {})
    text = f"{job.location} {job.remote_status} {job.work_model}".lower()
    if "remote" in text:
        return int(location_rules.get("remote", 5))
    if "hybrid" in text:
        return int(location_rules.get("hybrid", 5))
    for location, points in location_rules.items():
        if location in {"remote", "hybrid", "default"}:
            continue
        if location.lower() in text:
            return int(points)
    return int(location_rules.get("default", 1))


def _score_industry(company_context: dict[str, Any] | None, rules: dict[str, Any]) -> int:
    if not company_context:
        return 0
    text = " ".join(str(value) for value in company_context.values()).lower()
    for keyword, points in rules.get("industry_fit", {}).items():
        if keyword.lower() in text:
            return int(points)
    return 0


def _negative_penalty(text: str, rules: dict[str, Any]) -> tuple[int, list[str], bool]:
    negative = rules.get("negative_keywords", {})
    hard_matches = [keyword for keyword in negative.get("hard_exclude", []) if keyword.lower() in text]
    if hard_matches:
        return 100, hard_matches, True
    matches: list[str] = []
    total = 0
    for keyword, penalty in negative.get("penalties", {}).items():
        if keyword.lower() in text:
            matches.append(keyword)
            total += int(penalty)
    return total, matches, False


def score_job(job: JobPosting, rules: dict[str, Any], company_context: dict[str, Any] | None = None) -> JobPosting:
    text = _text_for_job(job)
    weights = rules.get("category_weights", {})
    positive = rules.get("positive_keywords", {})
    p_and_l_score, p_and_l_matches = _keyword_score(text, positive.get("p_and_l_path", []), int(weights.get("p_and_l_path_score", 20)))
    growth_score, growth_matches = _keyword_score(text, positive.get("growth_ownership", []), int(weights.get("growth_ownership_score", 20)))
    executive_score, executive_matches = _keyword_score(text, positive.get("executive_exposure", []), int(weights.get("executive_exposure_score", 15)))
    cadence_score, cadence_matches = _keyword_score(text, positive.get("operating_cadence", []), int(weights.get("operating_cadence_score", 10)))
    role_score = 0
    for keyword, points in rules.get("role_level_keywords", {}).items():
        if keyword.lower() in job.title.lower() or keyword.lower() == job.role_level.lower():
            role_score = max(role_score, int(points))
    comp_score = _score_comp(job, rules)
    location_score = _score_location(job, rules)
    industry_score = _score_industry(company_context, rules)
    penalty, penalty_matches, hard_exclude = _negative_penalty(text, rules)
    raw_total = p_and_l_score + growth_score + role_score + executive_score + cadence_score + comp_score + location_score + industry_score
    total = 0 if hard_exclude else max(0, min(100, raw_total - penalty))
    thresholds = rules.get("alert_thresholds", {})
    if hard_exclude:
        alert_tier = "exclude"
    elif total >= thresholds.get("immediate_review", 85):
        alert_tier = "immediate_review"
    elif total >= thresholds.get("strong_fit", 75):
        alert_tier = "strong_fit"
    elif total >= thresholds.get("track_only", 65):
        alert_tier = "track_only"
    else:
        alert_tier = "ignore"
    explanation_parts = []
    for label, matches in [("P&L", p_and_l_matches), ("growth", growth_matches), ("executive", executive_matches), ("cadence", cadence_matches)]:
        if matches:
            explanation_parts.append(f"{label}: {', '.join(matches[:5])}")
    if penalty_matches:
        explanation_parts.append(f"penalties: {', '.join(penalty_matches[:5])}")
    if not explanation_parts:
        explanation_parts.append("No major scoring keywords found")
    job.fit_score = total
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
    return job
