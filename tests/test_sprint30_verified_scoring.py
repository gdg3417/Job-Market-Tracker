from pathlib import Path

from src.company_context import build_company_context_map, company_context_for_name
from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _complete_job(*, company: str = "Acme Industrial", url: str = "https://careers.acme.com/jobs/123"):
    job = normalize_raw_job(
        {
            "company": company,
            "title": "Director, Commercial Strategy and Growth",
            "location": "Plano, TX Hybrid",
            "url": url,
            "source_primary": "gmail_alert",
            "description": (
                "Responsibilities include owning revenue growth, pricing strategy, margin expansion, and operating reviews. "
                "Lead a cross-functional team and report to the business unit president. Qualifications include a bachelor's "
                "degree and ten years of relevant experience. Manage a team and oversee executive business reviews."
            ),
        },
        source_primary="gmail_alert",
    )
    job.enrichment_status = "enriched"
    job.enrichment_source_url = url
    job.enrichment_match_confidence = 92
    return job


def test_fully_enriched_authoritative_job_receives_verified_score_without_salary():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job()
    context = {
        "industry_bucket": "manufacturing",
        "ownership_type": "PE-backed",
        "career_domain": "careers.acme.com",
    }

    scored = score_job(job, rules, company_context=context)

    assert scored.score_status == "verified"
    assert scored.verified_total_score is not None
    assert scored.verified_total_score >= scored.total_score
    assert scored.comp_score == 0
    assert "compensation_status=unknown" in scored.score_explanation
    assert "verified_score_basis=normalized_without_compensation" in scored.score_explanation
    assert "recommended_action=" in scored.score_explanation


def test_unknown_compensation_is_not_scored_the_same_as_known_below_floor_compensation():
    rules = load_scoring_rules(RULES_PATH)
    context = {"industry_bucket": "manufacturing", "career_domain": "careers.acme.com"}
    unknown = _complete_job(url="https://careers.acme.com/jobs/unknown-comp")
    below_floor = _complete_job(url="https://careers.acme.com/jobs/below-floor")
    below_floor.salary_min = 110000
    below_floor.salary_max = 120000
    below_floor.total_comp_estimate = 120000

    unknown_scored = score_job(unknown, rules, company_context=context)
    below_floor_scored = score_job(below_floor, rules, company_context=context)

    assert unknown_scored.score_status == "verified"
    assert below_floor_scored.score_status == "verified"
    assert unknown_scored.verified_total_score is not None
    assert below_floor_scored.verified_total_score is not None
    assert unknown_scored.verified_total_score > below_floor_scored.verified_total_score
    assert "compensation_status=unknown" in unknown_scored.score_explanation
    assert "compensation_status=unknown" not in below_floor_scored.score_explanation


def test_low_confidence_enrichment_cannot_create_verified_score():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job()
    job.enrichment_match_confidence = 79

    scored = score_job(job, rules, company_context={"career_domain": "careers.acme.com", "industry_bucket": "manufacturing"})

    assert scored.score_status == "partially_verified"
    assert scored.verified_total_score is None
    assert scored.verified_alert_tier == ""
    assert "below 80" in scored.score_explanation
    assert "authoritative matched source" in scored.score_explanation


def test_enriched_gmail_can_use_authoritative_canonical_url_when_lead_url_is_not_authoritative():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job(url="https://www.linkedin.com/jobs/view/1234567890")
    job.canonical_url = "https://careers.acme.com/jobs/123"

    scored = score_job(
        job,
        rules,
        company_context={"career_domain": "careers.acme.com", "industry_bucket": "manufacturing"},
    )

    assert scored.score_status == "verified"
    assert "authoritative_source=https://careers.acme.com/jobs/123" in scored.score_explanation


def test_company_root_domain_authorizes_a_different_career_subdomain():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job(url="https://careers.acme.com/jobs/root-domain")

    scored = score_job(
        job,
        rules,
        company_context={
            "career_search_url": "https://www.acme.com/careers",
            "industry_bucket": "manufacturing",
        },
    )

    assert scored.score_status == "verified"
    assert "authoritative_source=https://careers.acme.com/jobs/root-domain" in scored.score_explanation


def test_manual_search_without_match_confidence_is_not_trusted_as_manual_input():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX",
            "url": "https://careers.acme.com/jobs/789",
            "source_primary": "manual_search",
            "description": (
                "Responsibilities include commercial strategy, revenue growth, and operating reviews. Qualifications include "
                "a bachelor's degree and ten years of experience. Lead a team and report to the business unit president."
            ),
        }
    )
    job.enrichment_status = "enriched"
    job.enrichment_source_url = job.canonical_url

    scored = score_job(
        job,
        rules,
        company_context={"career_domain": "careers.acme.com", "industry_bucket": "manufacturing"},
    )

    assert scored.score_status == "partially_verified"
    assert scored.verified_total_score is None
    assert "match_confidence_status=not validated" in scored.score_explanation


def test_unknown_enrichment_source_requires_an_authoritative_domain_even_with_high_confidence():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX",
            "url": "https://aggregator.example/jobs/789",
            "source_primary": "aggregator_feed",
            "description": (
                "Responsibilities include commercial strategy, revenue growth, and operating reviews. Qualifications include "
                "a bachelor's degree and ten years of experience. Lead a team and report to the business unit president."
            ),
        }
    )
    job.enrichment_status = "enriched"
    job.enrichment_source_url = job.canonical_url
    job.enrichment_match_confidence = 95

    scored = score_job(
        job,
        rules,
        company_context={"career_domain": "careers.acme.com", "industry_bucket": "manufacturing"},
    )

    assert scored.score_status == "partially_verified"
    assert scored.verified_total_score is None
    assert "authoritative domain not confirmed" in scored.score_explanation


def test_company_context_does_not_award_job_level_ownership_points():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Strategic Planning",
            "location": "Plano, TX",
            "url": "https://careers.acme.com/jobs/456",
            "source_primary": "company_site",
            "description": (
                "Responsibilities include preparing market analyses and strategic plans. Qualifications include a bachelor's "
                "degree and ten years of experience. The role develops recommendations and presents periodic updates."
            ),
        }
    )
    context = {
        "career_domain": "careers.acme.com",
        "industry_bucket": "manufacturing",
        "p_and_l_path_rationale": "P&L ownership, business unit leadership, revenue growth, executive cadence",
    }

    scored = score_job(job, rules, company_context=context)

    assert scored.p_and_l_path_score == 0
    assert scored.growth_ownership_score == 0
    assert scored.executive_exposure_score == 0
    assert scored.operating_cadence_score == 0
    assert scored.industry_match_score > 0


def test_company_name_and_location_do_not_award_job_level_ownership_points():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "P&L Revenue Growth Executive Cadence LLC",
            "title": "Director, Strategic Planning",
            "location": "Business Unit, TX",
            "url": "https://careers.acme.com/jobs/incidental-text",
            "source_primary": "company_site",
            "description": (
                "Responsibilities include preparing market analyses and strategic plans for periodic planning meetings. "
                "Qualifications include a bachelor's degree and ten years of experience. The role develops recommendations "
                "and prepares written updates for internal stakeholders."
            ),
        }
    )

    scored = score_job(job, rules, company_context={"career_domain": "careers.acme.com"})

    assert scored.p_and_l_path_score == 0
    assert scored.growth_ownership_score == 0
    assert scored.executive_exposure_score == 0
    assert scored.operating_cadence_score == 0


def test_company_context_urls_and_notes_do_not_create_industry_points():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Strategic Planning",
            "location": "Plano, TX",
            "url": "https://careers.watch-manufacturing.example/jobs/context-noise",
            "source_primary": "company_site",
            "description": (
                "Responsibilities include preparing market analyses and strategic plans for periodic planning meetings. "
                "Qualifications include a bachelor's degree and ten years of experience. The role develops recommendations "
                "and prepares written updates for internal stakeholders."
            ),
        }
    )

    scored = score_job(
        job,
        rules,
        company_context={
            "career_domain": "careers.watch-manufacturing.example",
            "notes": "PE-backed manufacturing watch company",
            "p_and_l_path_rationale": "Consumer products and luxury goods pathway",
        },
    )

    assert scored.industry_match_score == 0


def test_company_preference_boost_is_auditable_and_capped():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Watchlist Co",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX",
            "url": "https://careers.watchlist.example/jobs/1",
            "source_primary": "company_site",
            "description": (
                "Responsibilities include commercial planning and strategic analysis. Qualifications include a bachelor's "
                "degree and ten years of experience. Lead cross-functional planning and management reviews."
            ),
        }
    )

    scored = score_job(
        job,
        rules,
        company_context={
            "career_domain": "careers.watchlist.example",
            "score_boost_points": 99,
        },
    )

    assert scored.industry_match_score == 5
    assert "requested 99, capped 5" in scored.score_explanation


def test_company_alias_resolves_full_context_without_blank_overwrite():
    contexts = build_company_context_map(
        [
            {
                "company_name": "Toyota Motor North America",
                "canonical_company_name": "Toyota Motor North America",
                "company_aliases": "Toyota North America; Toyota",
                "industry_bucket": "manufacturing",
                "career_domain": "careers.toyota.com",
            }
        ],
        [
            {
                "company_name": "Toyota North America",
                "priority_tier": "Tier 1",
                "industry_bucket": "",
            }
        ],
    )

    context = company_context_for_name("Toyota North America", contexts)

    assert context is not None
    assert context["industry_bucket"] == "manufacturing"
    assert context["priority_tier"] == "Tier 1"
    assert context["resolved_canonical_company_name"] == "Toyota Motor North America"
    assert context["context_match_type"] == "company_name"


def test_topgolf_and_toyota_sparse_leads_never_receive_verified_ignore_tier():
    rules = load_scoring_rules(RULES_PATH)
    examples = [
        ("Topgolf", "Sr Manager, Strategic Planning", "Dallas, TX"),
        ("Toyota North America", "National Manager, Product", "Plano, TX"),
    ]
    for company, title, location in examples:
        job = normalize_raw_job(
            {
                "company": company,
                "title": title,
                "location": location,
                "url": "https://www.linkedin.com/jobs/view/1234567890",
                "source_primary": "gmail_alert",
                "description": (
                    "Extracted from Gmail job alert. confidence=high. origin=linkedin; "
                    "extraction=linkedin_digest_card; linkedin_job_id=1234567890"
                ),
            },
            source_primary="gmail_alert",
        )
        scored = score_job(job, rules)
        assert scored.potential_priority == "high"
        assert scored.score_status == "provisional"
        assert scored.verified_total_score is None
        assert scored.verified_alert_tier == ""
        assert "score_status=provisional" in scored.score_explanation
        assert "enrichment_status=pending" in scored.score_explanation
        assert "recommended_action=Enrich or review" in scored.score_explanation
