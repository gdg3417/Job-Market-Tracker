from pathlib import Path

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def test_strong_commercial_strategy_job_scores_immediate_review():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy and Product Line Growth",
            "location": "Richardson, TX Hybrid",
            "salary": "$180,000 - $230,000",
            "description": "Own revenue growth, margin expansion, P&L pathway, business unit performance, operating cadence, cross-functional KPI reviews, executive leadership updates, and direct reports.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "manufacturing", "ownership_type": "PE-backed"})
    assert scored.total_score >= 85
    assert scored.alert_tier == "immediate_review"
    assert scored.fit_score > 0
    assert scored.p_and_l_path_score > 0
    assert scored.growth_ownership_score > 0
    assert "total=" in scored.score_explanation


def test_accounting_job_is_excluded():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Senior Accountant",
            "location": "Dallas, TX",
            "salary": "$100,000 - $120,000",
            "description": "Monthly close, journal entries, audit support, and balance sheet reconciliations.",
        }
    )
    scored = score_job(job, rules)
    assert scored.alert_tier == "exclude"
    assert scored.total_score == 0
    assert "hard_exclude=true" in scored.score_explanation


def test_missing_salary_does_not_break_scoring():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Manager, Business Operations",
            "location": "Plano, TX Hybrid",
            "description": "Run operating cadence and cross-functional business performance reviews for sales operations.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "industrial products"})
    assert scored.comp_score == 0
    assert scored.total_score >= 0
    assert scored.alert_tier in {"ignore", "track_only", "strong_fit", "immediate_review"}
    assert "missing salary" in scored.score_explanation


def test_reporting_only_bi_developer_is_downweighted():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Senior BI Developer",
            "location": "Remote",
            "salary": "$150,000 - $165,000",
            "description": "Dashboard maintenance, pure reporting, data pipeline support, and headcount tracking.",
        }
    )
    scored = score_job(job, rules)
    assert scored.total_score < 65
    assert scored.alert_tier == "ignore"
    assert "penalty=" in scored.score_explanation


def test_location_scoring_uses_commute_when_available():
    rules = load_scoring_rules(RULES_PATH)
    short_commute_job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Manager, Commercial Strategy",
            "location": "Dallas, TX",
            "commute_estimate_minutes": 12,
            "salary": "$160,000 - $185,000",
            "description": "Commercial strategy and revenue growth role.",
        }
    )
    long_commute_job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Manager, Commercial Strategy",
            "location": "Fort Worth, TX",
            "commute_estimate_minutes": 55,
            "salary": "$160,000 - $185,000",
            "description": "Commercial strategy and revenue growth role.",
        }
    )
    assert score_job(short_commute_job, rules).location_score > score_job(long_commute_job, rules).location_score


def test_rules_file_has_required_score_categories():
    rules = load_scoring_rules(RULES_PATH)
    weights = rules["category_weights"]
    assert sum(weights.values()) == 100
    for field_name in [
        "fit_score",
        "p_and_l_path_score",
        "growth_ownership_score",
        "executive_exposure_score",
        "operating_cadence_score",
        "comp_score",
        "location_score",
        "industry_match_score",
    ]:
        assert field_name in weights
