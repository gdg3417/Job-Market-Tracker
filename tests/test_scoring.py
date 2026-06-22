from pathlib import Path

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job
from src.sources.eml import read_eml
from src.sources.gmail_alerts import parse_job_alert_email, parsed_alerts_to_jobs


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


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


def test_generic_pmo_project_manager_stays_low():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme",
            "title": "Senior Program Manager, PMO",
            "location": "Dallas, TX",
            "salary": "$150,000 - $170,000",
            "description": "Generic PMO project manager role focused on project plan maintenance, status reporting, project tracking, and implementation governance.",
        }
    )
    scored = score_job(job, rules)
    assert scored.total_score < 65
    assert scored.alert_tier == "ignore"
    assert "penalty=" in scored.score_explanation


def test_low_value_support_billing_and_coordinator_roles_are_excluded():
    rules = load_scoring_rules(RULES_PATH)
    examples = [
        {"title": "Billing Specialist", "description": "Billing operations and invoice support."},
        {"title": "Insurance Operations Associate", "description": "Claims operations and policy processing."},
        {"title": "Project Coordinator", "description": "Meeting notes, status reporting, and project tracking."},
        {"title": "IT Infrastructure Support Specialist", "description": "Help desk, desktop support, and network tickets."},
    ]
    for raw in examples:
        job = normalize_raw_job({"company": "Acme", "location": "Dallas, TX", "salary": "$70,000 - $100,000", **raw})
        scored = score_job(job, rules)
        assert scored.alert_tier == "exclude"
        assert scored.total_score == 0
        assert "hard_exclude=true" in scored.score_explanation


def test_chief_of_staff_to_gm_scores_as_strategic_role():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Chief of Staff to the General Manager",
            "location": "Plano, TX Hybrid",
            "salary": "$180,000 - $215,000",
            "description": "Lead strategic initiatives, executive cadence, weekly business reviews, margin improvement, pricing strategy, and business unit operating performance with the GM and leadership team.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "industrial products", "ownership_type": "PE-backed"})
    assert scored.alert_tier in {"strong_fit", "immediate_review"}
    assert scored.total_score >= 75
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


def test_watch_brand_product_line_role_scores_immediate_review():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Fossil Group",
            "title": "Senior Manager, Category Management and Product Line Strategy",
            "location": "Richardson, TX Hybrid",
            "salary": "$190,000 - $220,000",
            "description": "Own product line profitability, category ownership, assortment strategy, pricing strategy, wholesale strategy, channel strategy, brand growth, inventory productivity, commercial operations, business performance, leadership team updates, and cross-functional operating cadence.",
        }
    )
    scored = score_job(
        job,
        rules,
        company_context={
            "industry_bucket": "watch, luxury goods, consumer products, retail wholesale",
            "ownership_type": "public company",
            "priority_tier": "Tier 1",
        },
    )
    assert scored.alert_tier == "immediate_review"
    assert scored.total_score >= 85
    assert scored.industry_match_score == 5
    assert "luxury goods" in scored.score_explanation or "watch" in scored.score_explanation


def test_rolex_client_advisor_role_is_excluded():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Rolex USA",
            "title": "Client Advisor",
            "location": "Dallas, TX",
            "salary": "$70,000 - $95,000",
            "description": "Boutique associate role focused on retail sales, client appointments, and brand ambassador duties.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "watch and luxury goods"})
    assert scored.alert_tier == "exclude"
    assert scored.total_score == 0
    assert "hard_exclude=true" in scored.score_explanation


def test_rolex_service_operations_role_is_not_blocked_by_watchmaker_exclusions():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Rolex USA",
            "title": "Director, Service Operations and Distribution",
            "location": "Dallas, TX",
            "salary": "$180,000 - $220,000",
            "description": "Lead regional service operations, distribution operations, business performance, executive leadership updates, operating cadence, and cross-functional margin improvement.",
        }
    )
    scored = score_job(job, rules, company_context={"industry_bucket": "watch, luxury goods, distribution"})
    assert scored.alert_tier in {"track_only", "strong_fit", "immediate_review"}
    assert scored.total_score > 0
    assert "hard_exclude=true" not in scored.score_explanation


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


def _sparse_gmail_job(title: str, company: str = "Acme", location: str = "Dallas, TX"):
    return normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": location,
            "source_primary": "gmail_alert",
            "description": "Extracted from Gmail job alert. confidence=high. origin=linkedin; extraction=linkedin_digest_card; linkedin_job_id=1234567890",
        },
        source_primary="gmail_alert",
    )


def test_sparse_topgolf_and_toyota_titles_are_marked_for_manual_review_without_score_inflation():
    rules = load_scoring_rules(RULES_PATH)
    examples = [
        ("Sr Manager, Strategic Planning", "Topgolf", "Dallas, TX"),
        ("National Manager, Product", "Toyota North America", "Plano, TX"),
    ]
    for title, company, location in examples:
        gmail_job = _sparse_gmail_job(title, company, location)
        baseline_job = normalize_raw_job(
            {
                "company": company,
                "title": title,
                "location": location,
                "description": gmail_job.description_text,
            },
            source_primary="manual",
        )
        scored_gmail = score_job(gmail_job, rules)
        scored_baseline = score_job(baseline_job, rules)
        assert scored_gmail.total_score == scored_baseline.total_score
        assert scored_gmail.alert_tier == scored_baseline.alert_tier
        assert "manual_review=true" in scored_gmail.score_explanation
        assert "review_reason=sparse_gmail_high_signal_title" in scored_gmail.score_explanation
        assert "manual_review=true" not in scored_baseline.score_explanation


def test_complete_gmail_posting_is_scored_normally_and_not_marked_sparse():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX Hybrid",
            "source_primary": "gmail_alert",
            "salary": "$180,000 - $220,000",
            "description": "Own revenue growth, pricing, margin expansion, operating cadence, and executive reviews for a business unit. Hybrid three days in office.",
        },
        source_primary="gmail_alert",
    )
    scored = score_job(job, rules)
    assert "manual_review=true" not in scored.score_explanation
    assert "review_reason=sparse_gmail_high_signal_title" not in scored.score_explanation


def test_entry_level_strategy_title_is_not_promoted_for_review():
    rules = load_scoring_rules(RULES_PATH)
    scored = score_job(_sparse_gmail_job("Corporate Strategy Analyst"), rules)
    assert "manual_review=true" not in scored.score_explanation


def test_hard_excluded_sparse_role_remains_excluded():
    rules = load_scoring_rules(RULES_PATH)
    scored = score_job(_sparse_gmail_job("Manager, Strategic Planning and Billing Specialist"), rules)
    assert scored.total_score == 0
    assert scored.alert_tier == "exclude"
    assert "hard_exclude=true" in scored.score_explanation
    assert "manual_review=true" not in scored.score_explanation


def test_linkedin_fixture_roles_receive_sparse_gmail_review_treatment():
    rules = load_scoring_rules(RULES_PATH)
    fixture_expectations = [
        ("linkedin_topgolf.eml", "Sr Manager, Strategic Planning", "Topgolf"),
        ("linkedin_toyota.eml", "National Manager, Product", "Toyota North America"),
    ]
    for fixture_name, title, company in fixture_expectations:
        email = read_eml(FIXTURES / fixture_name)
        jobs = parsed_alerts_to_jobs(parse_job_alert_email(email), scoring_rules=rules)
        target = next(job for job in jobs if job.title == title and job.company == company)
        assert "manual_review=true" in target.score_explanation
        assert "review_reason=sparse_gmail_high_signal_title" in target.score_explanation


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
    review_rules = rules["sparse_gmail_review"]
    assert "Strategic Planning" in review_rules["priority_title_phrases"]
    assert "National Manager" in review_rules["seniority_phrases"]
