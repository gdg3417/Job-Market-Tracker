from pathlib import Path
from urllib.parse import urlsplit

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _score_authoritative(company: str, title: str, description: str, url: str, location: str = "Plano, TX Hybrid"):
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": location,
            "url": url,
            "source_primary": "company_site",
            "description": description,
        }
    )
    return score_job(
        job,
        rules,
        company_context={
            "industry_bucket": "manufacturing",
            "career_domain": urlsplit(url).hostname,
        },
    )


def test_bsn_sports_pricing_strategy_calibrates_as_verified_strong_fit_without_salary():
    scored = _score_authoritative(
        "BSN Sports",
        "Senior Manager, Pricing Strategy",
        (
            "Responsibilities include owning pricing strategy, price realization, margin expansion, and profitable revenue growth "
            "for a national product portfolio. Lead cross-functional go-to-market planning, operating reviews, and executive "
            "business reviews with senior leadership. Manage a team and partner with business unit leaders on product line "
            "profitability and category strategy. Qualifications include a bachelor's degree and ten years of commercial experience."
        ),
        "https://careers.bsnsports.example/jobs/pricing-strategy",
    )

    assert scored.score_status == "verified"
    assert scored.verified_total_score is not None
    assert scored.verified_total_score >= scored.total_score
    assert scored.verified_alert_tier in {"strong_fit", "immediate_review"}
    assert scored.comp_score == 0
    assert "compensation_status=unknown" in scored.score_explanation
    assert "verified_score_basis=normalized_without_compensation" in scored.score_explanation


def test_ericsson_strategic_business_insights_calibrates_above_weak_strategy_title():
    ericsson = _score_authoritative(
        "Ericsson",
        "Manager, Strategic Business Insights",
        (
            "Responsibilities include leading business insights, commercial analytics, revenue growth strategy, and performance "
            "management for a business unit. Drive cross-functional operating reviews, monthly business reviews, and executive "
            "leadership recommendations. Partner with the president and senior leadership on market expansion, value creation, "
            "and margin improvement. Manage a team. Qualifications include a bachelor's degree and eight years of experience."
        ),
        "https://careers.ericsson.example/jobs/strategic-business-insights",
    )
    weak = _score_authoritative(
        "Generic Services Co",
        "Director, Strategy Program Management",
        (
            "Responsibilities include running the project management office, project tracking, project plans, status reporting, "
            "IT infrastructure coordination, and dashboard maintenance. The role prepares administrative updates and manages "
            "implementation timelines. Qualifications include a bachelor's degree and ten years of program management experience."
        ),
        "https://careers.generic.example/jobs/strategy-program-management",
    )

    assert ericsson.score_status == "verified"
    assert weak.score_status == "verified"
    assert ericsson.verified_total_score is not None
    assert weak.verified_total_score is not None
    assert ericsson.verified_total_score > weak.verified_total_score
    assert weak.verified_alert_tier == "ignore"


def test_promising_sparse_topgolf_remains_visible_while_fully_described_weak_role_scores_low():
    rules = load_scoring_rules(RULES_PATH)
    topgolf = normalize_raw_job(
        {
            "company": "Topgolf",
            "title": "Sr Manager, Strategic Planning",
            "location": "Dallas, TX",
            "url": "https://www.linkedin.com/jobs/view/4417965465",
            "source_primary": "gmail_alert",
            "description": (
                "Extracted from Gmail job alert. confidence=high. origin=linkedin; "
                "extraction=linkedin_digest_card; linkedin_job_id=4417965465"
            ),
        },
        source_primary="gmail_alert",
    )
    topgolf = score_job(topgolf, rules)
    weak = _score_authoritative(
        "Generic Services Co",
        "Manager, Strategy Operations",
        (
            "Responsibilities include generic PMO administration, project tracking, status reporting, budget consolidation, "
            "headcount tracking, and dashboard maintenance. The role coordinates project plans and day-to-day administrative "
            "operations. Qualifications include a bachelor's degree and seven years of project management experience."
        ),
        "https://careers.generic.example/jobs/strategy-operations",
    )

    assert topgolf.potential_priority == "high"
    assert topgolf.score_status == "provisional"
    assert topgolf.enrichment_status == "pending"
    assert topgolf.verified_total_score is None
    assert weak.score_status == "verified"
    assert weak.verified_alert_tier == "ignore"


def test_hard_excluded_accounting_role_remains_excluded_after_full_evidence():
    scored = _score_authoritative(
        "Generic Industrial",
        "Senior Accountant, Strategic Projects",
        (
            "Responsibilities include journal entries, monthly close, audit support, and accounting operations. The role prepares "
            "variance commentary and supports project reporting. Qualifications include a bachelor's degree in accounting and "
            "seven years of experience."
        ),
        "https://careers.generic-industrial.example/jobs/senior-accountant",
    )

    assert scored.score_status == "excluded"
    assert scored.verified_total_score == 0
    assert scored.verified_alert_tier == "exclude"
    assert "recommended_action=Do not pursue" in scored.score_explanation
