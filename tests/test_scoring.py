from pathlib import Path

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def test_strong_commercial_strategy_job_scores():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Director, Commercial Strategy and Product Line Growth",
            "location": "Richardson, TX Hybrid",
            "salary": "$180,000 - $230,000",
            "description": "Own revenue growth, margin expansion, P&L pathway, operating cadence, and executive leadership updates.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "manufacturing"})
    assert scored.total_score >= 75
    assert scored.alert_tier in {"strong_fit", "immediate_review"}
    assert scored.p_and_l_path_score > 0
    assert scored.growth_ownership_score > 0


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
