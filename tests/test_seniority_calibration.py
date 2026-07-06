from pathlib import Path

from src.normalize import infer_role_level, normalize_raw_job
from src.review_queue import REVIEW_QUEUE_HEADERS, build_review_queue_rows
from src.scoring import load_scoring_rules, score_job
from src.seniority import evaluate_seniority_fit

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"

STRONG_DESCRIPTION = (
    "Own revenue growth, margin expansion, P&L pathway, business unit performance, "
    "operating cadence, cross-functional KPI reviews, executive leadership updates, "
    "pricing strategy, strategic planning, and team leadership."
)


def _job(title: str, **overrides):
    values = {
        "company": "Acme Industrial",
        "title": title,
        "location": "Plano, TX Hybrid",
        "salary": "$180,000 - $220,000",
        "description": STRONG_DESCRIPTION,
        "canonical_url": "https://example.com/jobs/123",
    }
    values.update(overrides)
    return normalize_raw_job(values)


def _score(title: str, **kwargs):
    return score_job(_job(title, **kwargs), load_scoring_rules(RULES_PATH), company_context=kwargs.get("company_context"))


def _record(job):
    row = build_review_queue_rows([job])[0]
    return dict(zip(REVIEW_QUEUE_HEADERS, row))


def test_manager_and_senior_manager_receive_target_level_treatment():
    manager = _score("Manager, Commercial Strategy")
    senior_manager = _score("Senior Manager, Commercial Strategy")

    assert manager.role_level == "Manager"
    assert senior_manager.role_level == "Senior Manager"
    assert "seniority_fit=target" in manager.score_explanation
    assert "seniority_reason=target_seniority_manager" in manager.score_explanation
    assert "seniority_reason=target_seniority_senior_manager" in senior_manager.score_explanation
    assert manager.potential_priority in {"medium", "high"}
    assert senior_manager.potential_priority in {"medium", "high"}


def test_director_role_is_stretch_not_rejected_by_default():
    director = _score("Director, Commercial Strategy")

    assert director.role_level == "Director"
    assert director.alert_tier != "exclude"
    assert "hard_exclude=true" not in director.score_explanation
    assert "seniority_fit=stretch" in director.score_explanation
    assert "seniority_reason=stretch_seniority_director" in director.score_explanation


def test_director_at_pe_backed_company_remains_eligible():
    rules = load_scoring_rules(RULES_PATH)
    director = score_job(
        _job("Director, Commercial Strategy"),
        rules,
        company_context={"ownership_type": "PE-backed", "industry_bucket": "industrial products"},
    )

    assert director.alert_tier in {"track_only", "strong_fit", "immediate_review"}
    assert director.potential_priority in {"medium", "high"}
    assert "seniority_fit=stretch" in director.score_explanation
    assert "seniority_reason=stretch_seniority_director_context_viable" in director.score_explanation


def test_senior_director_and_vp_are_penalized_below_viable_queue():
    senior_director = _score("Senior Director, Commercial Strategy")
    vp = _score("VP, Commercial Strategy")

    assert senior_director.total_score < 50
    assert vp.total_score < 50
    assert senior_director.potential_priority == "low"
    assert vp.potential_priority == "low"
    assert "seniority_reason=likely_too_senior_senior_director" in senior_director.score_explanation
    assert "seniority_reason=likely_too_senior_vp" in vp.score_explanation
    assert build_review_queue_rows([senior_director, vp]) == []


def test_svp_evp_and_c_suite_roles_are_hard_excluded():
    for title in ["SVP, Commercial Strategy", "EVP, Strategy", "Chief Financial Officer"]:
        scored = _score(title)
        assert scored.alert_tier == "exclude"
        assert scored.total_score == 0
        assert scored.potential_priority == "excluded"
        assert "hard_exclude=true" in scored.score_explanation


def test_chief_of_staff_remains_eligible_and_context_dependent():
    chief_of_staff = _score("Chief of Staff to the General Manager")

    assert chief_of_staff.role_level == "Chief of Staff"
    assert chief_of_staff.alert_tier in {"track_only", "strong_fit", "immediate_review"}
    assert chief_of_staff.potential_priority in {"medium", "high"}
    assert "seniority_fit=context_dependent" in chief_of_staff.score_explanation
    assert "seniority_reason=context_dependent_chief_of_staff" in chief_of_staff.score_explanation


def test_head_of_strategy_routes_to_manual_review_not_rejection():
    head_of = _score("Head of Strategy")

    assert head_of.role_level == "Head of"
    assert head_of.alert_tier != "exclude"
    assert "seniority_fit=manual_review" in head_of.score_explanation
    assert "seniority_reason=manual_review_head_of" in head_of.score_explanation
    assert "manual_review=true" in head_of.score_explanation


def test_seniority_penalties_override_good_keyword_matches():
    manager = _score("Manager, Commercial Strategy")
    vp = _score("VP, Commercial Strategy")

    assert manager.total_score > vp.total_score
    assert vp.alert_tier == "ignore"
    assert vp.potential_priority == "low"


def test_review_queue_exposes_seniority_fit_for_human_review():
    director = _score("Director, Commercial Strategy")
    record = _record(director)

    assert record["role_level"] == "Director"
    assert record["seniority_fit"] == "stretch"
    assert record["seniority_reason"] == "stretch_seniority_director"


def test_seniority_parser_distinguishes_target_stretch_and_too_senior_titles():
    assert infer_role_level("Senior Manager, Revenue Strategy") == "Senior Manager"
    assert infer_role_level("Director of Product Strategy") == "Director"
    assert infer_role_level("Senior Director, Strategy") == "Senior Director"
    assert infer_role_level("VP, Revenue Strategy") == "VP"
    assert evaluate_seniority_fit("Head of Strategy").seniority_fit == "manual_review"
