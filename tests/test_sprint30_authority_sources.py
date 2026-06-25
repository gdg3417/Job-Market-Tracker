from pathlib import Path

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _complete_job(*, source_primary: str, url: str):
    return normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "Plano, TX Hybrid",
            "url": url,
            "source_primary": source_primary,
            "description": (
                "Responsibilities include commercial strategy, revenue growth, pricing, margin expansion, and operating reviews. "
                "Lead cross-functional planning and report recommendations to senior leadership. Qualifications include a bachelor's "
                "degree and ten years of relevant experience. Manage a team and oversee recurring business reviews."
            ),
        },
        source_primary=source_primary,
    )


def test_unrecognized_source_requires_match_confidence_even_on_configured_company_domain():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job(
        source_primary="aggregator_feed",
        url="https://careers.acme.com/jobs/authority-check",
    )

    scored = score_job(
        job,
        rules,
        company_context={"career_domain": "careers.acme.com", "industry_bucket": "manufacturing"},
    )

    assert scored.score_status == "partially_verified"
    assert scored.verified_total_score is None
    assert "match_confidence_status=not validated" in scored.score_explanation
    assert "authoritative matched source" in scored.score_explanation


def test_recognized_greenhouse_source_can_verify_without_enrichment_match_confidence():
    rules = load_scoring_rules(RULES_PATH)
    job = _complete_job(
        source_primary="greenhouse",
        url="https://boards.greenhouse.io/acme/jobs/123456",
    )

    scored = score_job(job, rules, company_context={"industry_bucket": "manufacturing"})

    assert scored.score_status == "verified"
    assert scored.verified_total_score is not None
    assert "match_confidence_status=trusted authoritative source" in scored.score_explanation
