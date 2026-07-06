from pathlib import Path

import pytest

from src.company_exclusions import evaluate_company_exclusion, normalize_company_name
from src.normalize import normalize_raw_job
from src.review_workflow import build_review_dashboard_sections
from src.scoring import load_scoring_rules, score_job

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _strategic_job(company: str, title: str = "Senior Manager, Commercial Strategy"):
    return normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": "Dallas, TX Hybrid",
            "salary": "$180,000 - $220,000",
            "url": "https://example.com/job/123",
            "description": "Own revenue growth, pricing strategy, margin expansion, operating cadence, executive leadership updates, and cross-functional business performance.",
        }
    )


def _score(company: str, title: str = "Senior Manager, Commercial Strategy"):
    rules = load_scoring_rules(RULES_PATH)
    return score_job(_strategic_job(company, title), rules, company_context={"industry_bucket": "manufacturing"})


def test_exact_blocked_company_is_rejected():
    scored = _score("Gartner")
    assert scored.alert_tier == "exclude"
    assert scored.total_score == 0
    assert scored.potential_priority == "excluded"
    assert scored.score_status == "excluded"
    assert "company_exclusion=true" in scored.score_explanation
    assert "company_exclusion_reason=blocked_company" in scored.score_explanation
    assert "company_exclusion_match=Gartner" in scored.score_explanation


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("deloitte consulting llp", "Deloitte"),
        ("EY-Parthenon", "EY"),
        ("Ernst & Young", "EY"),
        ("A&M", "Alvarez & Marsal"),
        ("Alvarez and Marsal", "Alvarez & Marsal"),
        ("PWC Advisory", "PwC"),
        ("PricewaterhouseCoopers", "PwC"),
        ("L.E.K. Consulting", "L.E.K. Consulting"),
    ],
)
def test_company_alias_and_punctuation_matching(company, expected):
    scored = _score(company)
    assert scored.alert_tier == "exclude"
    assert scored.total_score == 0
    assert f"company_exclusion_match={expected}" in scored.score_explanation
    assert "hard_exclude=true" in scored.score_explanation


@pytest.mark.parametrize(
    "company",
    [
        "Swooped",
        "Deloitte",
        "Deloitte Consulting",
        "EY",
        "Ernst & Young",
        "Alvarez & Marsal",
        "A&M",
        "KPMG",
        "Bain",
        "Bain & Company",
        "BCG",
        "Boston Consulting Group",
        "McKinsey",
        "Accenture",
        "PwC",
        "PricewaterhouseCoopers",
        "Oliver Wyman",
        "LEK",
        "L.E.K. Consulting",
    ],
)
def test_consulting_firm_suppression(company):
    scored = _score(company)
    assert scored.alert_tier == "exclude"
    assert scored.verified_alert_tier == "exclude"
    assert "company_exclusion_category=" in scored.score_explanation
    assert "hard_exclude=true" in scored.score_explanation


def test_company_normalization_handles_case_spacing_and_legal_suffixes():
    assert normalize_company_name("  Deloitte Consulting, LLP ") == "deloitte consulting"
    assert normalize_company_name("L.E.K. Consulting") == "l e k consulting"
    assert normalize_company_name("A&M") == "a and m"


def test_operating_company_with_strategy_consulting_title_is_not_rejected():
    scored = _score("Acme Industrial", title="Manager, Strategy Consulting and Business Operations")
    assert scored.alert_tier != "exclude"
    assert scored.total_score > 0
    assert "company_exclusion=true" not in scored.score_explanation


def test_pe_backed_operating_company_with_advisory_language_is_not_rejected():
    rules = load_scoring_rules(RULES_PATH)
    job = _strategic_job("Portfolio Manufacturing Co", title="Senior Manager, Transformation Advisory")
    job.description_text += " Advisory support for a PE-backed operating company, value creation, and business unit performance."
    scored = score_job(
        job,
        rules,
        company_context={"industry_bucket": "industrial products", "ownership_type": "PE-backed"},
    )
    assert scored.alert_tier != "exclude"
    assert scored.total_score > 0
    assert "company_exclusion=true" not in scored.score_explanation


def test_blocked_companies_do_not_surface_as_viable_review_queue_items():
    blocked = _score("McKinsey", title="Senior Manager, Commercial Strategy")
    eligible = _score("Acme Industrial", title="Senior Manager, Commercial Strategy")
    eligible.review_status = "review_now"

    dashboard_rows = build_review_dashboard_sections([blocked, eligible])
    row_text = "\n".join(" | ".join(str(cell) for cell in row) for row in dashboard_rows)

    assert "Acme Industrial" in row_text
    assert "McKinsey" not in row_text
    assert blocked.alert_tier == "exclude"
    assert blocked.score_status == "excluded"


def test_company_exclusion_matcher_does_not_block_ambiguous_advisory_language_without_company_match():
    rules = load_scoring_rules(RULES_PATH)
    match = evaluate_company_exclusion("Advisory Operations Group", rules["company_exclusions"])
    assert not match.blocked
