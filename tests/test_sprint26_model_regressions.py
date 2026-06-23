from copy import deepcopy
from pathlib import Path

from src.normalize import normalize_raw_job
from src.potential_priority import calculate_evidence_completeness, evaluate_potential_priority
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _potential_rules() -> dict:
    return load_scoring_rules(RULES_PATH)["potential_priority"]


def _strategy_job():
    return normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Senior Manager, Commercial Strategy",
            "location": "Dallas, TX",
            "url": "https://example.com/jobs/commercial-strategy",
            "description": "Lead commercial strategy and revenue growth for a product portfolio.",
        }
    )


def test_source_quality_high_does_not_award_target_company_bonus():
    rules = _potential_rules()
    job = _strategy_job()

    source_quality_score, _, source_quality_reason = evaluate_potential_priority(
        job,
        rules,
        company_context={"source_quality": "high"},
    )
    target_score, _, target_reason = evaluate_potential_priority(
        job,
        rules,
        company_context={"priority_tier": "high"},
    )

    assert "target_company=0" in source_quality_reason
    assert "target_company=5" in target_reason
    assert target_score == source_quality_score + 5


def test_only_meaningful_company_context_adds_evidence_points():
    rules = _potential_rules()
    job = _strategy_job()

    source_quality_score, source_quality_evidence = calculate_evidence_completeness(
        job,
        rules,
        company_context={"source_quality": "high"},
    )
    industry_score, industry_evidence = calculate_evidence_completeness(
        job,
        rules,
        company_context={"industry_bucket": "manufacturing"},
    )

    assert "company context" not in source_quality_evidence
    assert "company context" in industry_evidence
    assert industry_score == source_quality_score + 10


def test_configured_category_weights_cap_potential_components():
    rules = _potential_rules()
    capped_rules = deepcopy(rules)
    capped_rules["weights"] = {
        "relevant_seniority": 3,
        "relevant_role_family": 4,
        "strategic_ownership_signals": 5,
        "company_industry_fit": 6,
        "location_work_model": 7,
        "target_company_preference": 2,
    }

    score, _, reason = evaluate_potential_priority(
        _strategy_job(),
        capped_rules,
        company_context={"industry_bucket": "manufacturing", "priority_tier": "high"},
    )

    assert score == 27
    assert "seniority=3" in reason
    assert "role_family=4" in reason
    assert "ownership=5" in reason
    assert "company=6" in reason
    assert "location=7" in reason
    assert "target_company=2" in reason


def test_verified_rescore_preserves_completed_enrichment_status():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX Hybrid",
            "salary": "$180,000 to $220,000",
            "url": "https://example.com/jobs/verified-enriched",
            "description": (
                "Responsibilities include owning revenue growth, pricing strategy, margin expansion, and operating reviews. "
                "Lead a cross-functional team and report to the business unit president. Qualifications include a bachelor's "
                "degree and ten years of relevant experience. Manage a team and oversee executive business reviews."
            ),
        }
    )
    job.enrichment_status = "enriched"
    job.enrichment_completed_at = "2026-06-23T12:00:00Z"
    job.enrichment_source_url = "https://example.com/jobs/verified-enriched"

    scored = score_job(job, rules, company_context={"industry_bucket": "manufacturing"})

    assert scored.score_status == "verified"
    assert scored.enrichment_status == "enriched"
    assert scored.enrichment_completed_at == "2026-06-23T12:00:00Z"
    assert scored.enrichment_source_url == "https://example.com/jobs/verified-enriched"


def test_reopened_high_potential_job_requeues_closed_enrichment():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Topgolf",
            "title": "Sr Manager, Strategic Planning",
            "location": "Dallas, TX",
            "url": "https://www.linkedin.com/jobs/view/4427955315",
            "source_primary": "gmail_alert",
            "source_job_id": "4427955315",
            "description": (
                "Extracted from Gmail job alert. confidence=high. origin=linkedin; "
                "extraction=linkedin_digest_card; linkedin_job_id=4427955315"
            ),
        },
        source_primary="gmail_alert",
    )
    job.status = "reopened"
    job.enrichment_status = "closed"

    scored = score_job(job, rules)

    assert scored.potential_priority == "high"
    assert scored.score_status == "provisional"
    assert scored.enrichment_status == "pending"
    assert scored.enrichment_priority == "high"
