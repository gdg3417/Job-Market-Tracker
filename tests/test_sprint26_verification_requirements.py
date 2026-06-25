from pathlib import Path

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"
AUTHORITATIVE_CONTEXT = {"industry_bucket": "manufacturing", "career_domain": "example.com"}


def test_complete_job_without_location_or_remote_designation_is_not_verified():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "",
            "salary": "$180,000 to $220,000",
            "url": "https://example.com/jobs/strategy-location-required",
            "source_primary": "company_site",
            "description": (
                "Responsibilities include owning revenue growth, pricing strategy, margin expansion, and operating reviews. "
                "Lead a cross-functional team and report to the business unit president. Qualifications include a bachelor's "
                "degree and ten years of relevant experience. Manage a team and oversee executive business reviews."
            ),
        }
    )

    scored = score_job(job, rules, company_context=AUTHORITATIVE_CONTEXT)

    assert scored.evidence_completeness_score >= 70
    assert scored.score_status == "partially_verified"
    assert scored.verified_total_score is None
    assert "location or remote designation" in scored.score_explanation


def test_remote_designation_can_satisfy_verified_location_requirement():
    rules = load_scoring_rules(RULES_PATH)
    job = normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": "Director, Commercial Strategy",
            "location": "",
            "salary": "$180,000 to $220,000",
            "url": "https://example.com/jobs/strategy-remote",
            "source_primary": "company_site",
            "description": (
                "This is a remote role. Responsibilities include owning revenue growth, pricing strategy, margin expansion, "
                "and operating reviews. Lead a cross-functional team and report to the business unit president. Qualifications "
                "include a bachelor's degree and ten years of relevant experience. Manage a team and oversee executive business reviews."
            ),
        }
    )

    scored = score_job(job, rules, company_context=AUTHORITATIVE_CONTEXT)

    assert scored.remote_status == "remote"
    assert scored.work_model == "remote"
    assert scored.score_status == "verified"
    assert scored.verified_total_score == scored.total_score
