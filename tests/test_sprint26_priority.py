from copy import deepcopy
from pathlib import Path

from src.dashboard import build_dashboard_values, build_digest_rows
from src.dedupe import merge_job
from src.models import JOB_FIELDS, JobPosting
from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _sparse_job(title: str, company: str, location: str, job_id: str) -> JobPosting:
    return normalize_raw_job(
        {
            "company": company,
            "title": title,
            "location": location,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
            "source_primary": "gmail_alert",
            "source_job_id": job_id,
            "description": (
                "Extracted from Gmail job alert. confidence=high. origin=linkedin; "
                f"extraction=linkedin_digest_card; linkedin_job_id={job_id}"
            ),
        },
        source_primary="gmail_alert",
    )


def test_topgolf_and_toyota_are_high_potential_provisional_and_pending():
    rules = load_scoring_rules(RULES_PATH)
    jobs = [
        score_job(_sparse_job("Sr Manager, Strategic Planning", "Topgolf", "Dallas, TX", "4427955315"), rules),
        score_job(_sparse_job("National Manager, Product", "Toyota North America", "Plano, TX", "4430066274"), rules),
    ]

    for job in jobs:
        assert job.potential_priority == "high"
        assert job.potential_priority_score >= 70
        assert job.evidence_completeness_score < 40
        assert job.score_status == "provisional"
        assert job.verified_total_score is None
        assert job.verified_alert_tier == ""
        assert job.enrichment_status == "pending"
        assert job.enrichment_priority == "high"


def test_junior_generic_strategy_role_is_not_high_priority():
    rules = load_scoring_rules(RULES_PATH)
    job = score_job(
        _sparse_job("Corporate Strategy Analyst", "Acme", "Dallas, TX", "1000000001"),
        rules,
    )

    assert job.potential_priority in {"medium", "low"}
    assert job.potential_priority_score < 70
    assert job.enrichment_status == "not_required"


def test_hard_exclusion_remains_excluded_across_both_models():
    rules = load_scoring_rules(RULES_PATH)
    job = score_job(
        _sparse_job("Manager, Strategic Planning and Billing Specialist", "Acme", "Dallas, TX", "1000000002"),
        rules,
    )

    assert job.total_score == 0
    assert job.alert_tier == "exclude"
    assert job.potential_priority == "excluded"
    assert job.score_status == "excluded"
    assert job.verified_total_score == 0
    assert job.verified_alert_tier == "exclude"
    assert job.enrichment_status == "not_required"


def test_missing_salary_does_not_reduce_potential_priority():
    rules = load_scoring_rules(RULES_PATH)
    base = {
        "company": "Acme Industrial",
        "title": "Senior Manager, Commercial Strategy",
        "location": "Plano, TX Hybrid",
        "url": "https://example.com/jobs/strategy-1",
        "description": "Lead commercial strategy and revenue growth planning for a product portfolio.",
    }
    without_salary = score_job(normalize_raw_job(base), rules)
    with_salary = score_job(normalize_raw_job({**base, "salary": "$170,000 to $200,000"}), rules)

    assert without_salary.potential_priority_score == with_salary.potential_priority_score
    assert without_salary.potential_priority == with_salary.potential_priority
    assert without_salary.comp_score == 0
    assert with_salary.comp_score > without_salary.comp_score


def test_missing_description_lowers_evidence_completeness():
    rules = load_scoring_rules(RULES_PATH)
    common = {
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX Hybrid",
        "url": "https://example.com/jobs/strategy-2",
        "salary": "$180,000 to $220,000",
    }
    sparse = score_job(normalize_raw_job(common), rules, company_context={"industry_bucket": "manufacturing"})
    complete = score_job(
        normalize_raw_job(
            {
                **common,
                "description": (
                    "Responsibilities include owning revenue growth, pricing strategy, margin expansion, and operating reviews. "
                    "Lead a cross-functional team and report to the business unit president. Qualifications include a bachelor's "
                    "degree and ten years of relevant experience. Hybrid three days in office."
                ),
            }
        ),
        rules,
        company_context={"industry_bucket": "manufacturing"},
    )

    assert complete.evidence_completeness_score > sparse.evidence_completeness_score
    assert complete.score_status == "verified"
    assert complete.verified_total_score == complete.total_score
    assert complete.verified_alert_tier == complete.alert_tier
    assert complete.enrichment_status == "not_required"


def test_potential_priority_does_not_change_existing_fit_calculation():
    rules = load_scoring_rules(RULES_PATH)
    without_priority = deepcopy(rules)
    without_priority["potential_priority"] = {}
    raw = {
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy and Product Line Growth",
        "location": "Richardson, TX Hybrid",
        "salary": "$180,000 to $230,000",
        "url": "https://example.com/jobs/strategy-3",
        "description": (
            "Own revenue growth, margin expansion, P&L pathway, business unit performance, operating cadence, "
            "cross-functional KPI reviews, executive leadership updates, and direct reports."
        ),
    }
    baseline = score_job(normalize_raw_job(raw), without_priority, company_context={"industry_bucket": "manufacturing"})
    sprint26 = score_job(normalize_raw_job(raw), rules, company_context={"industry_bucket": "manufacturing"})

    assert sprint26.total_score == baseline.total_score
    assert sprint26.alert_tier == baseline.alert_tier
    assert sprint26.fit_score == baseline.fit_score
    assert sprint26.p_and_l_path_score == baseline.p_and_l_path_score
    assert sprint26.growth_ownership_score == baseline.growth_ownership_score


def test_dashboard_routes_high_potential_jobs_without_calling_them_ignore():
    rules = load_scoring_rules(RULES_PATH)
    topgolf = score_job(
        _sparse_job("Sr Manager, Strategic Planning", "Topgolf", "Dallas, TX", "4427955315"),
        rules,
    )
    rows = build_digest_rows([topgolf], as_of=topgolf.first_seen_date)

    assert len(rows) == 1
    assert rows[0][0] == "High-potential roles awaiting enrichment"
    assert rows[0][10] == "pending_verification"
    assert rows[0][20] == "high"
    assert rows[0][22] == "provisional"
    assert rows[0][25] == "pending"

    dashboard = build_dashboard_values([topgolf], digest_rows=rows, rejected_job_rows=[])
    flattened = "\n".join(str(cell) for row in dashboard for cell in row)
    assert "Enrich high-potential roles" in flattened
    assert "High-potential roles awaiting enrichment" in flattened
    assert "Verified strong fits" in flattened


def test_duplicate_merge_preserves_verified_score_and_stronger_evidence():
    existing = JobPosting(
        job_key="job-verified",
        company="Acme Industrial",
        title="Director, Commercial Strategy",
        location="Plano, TX",
        canonical_url="https://example.com/jobs/verified",
        total_score=88,
        alert_tier="immediate_review",
        score_explanation="total=88; tier=immediate_review",
        potential_priority_score=90,
        potential_priority="high",
        evidence_completeness_score=90,
        score_status="verified",
        verified_total_score=88,
        verified_alert_tier="immediate_review",
        enrichment_status="enriched",
    )
    incoming = JobPosting(
        job_key="job-verified",
        company="Acme Industrial",
        title="Director, Commercial Strategy",
        location="Plano, TX",
        canonical_url="https://example.com/jobs/verified",
        total_score=22,
        alert_tier="ignore",
        score_explanation="total=22; tier=ignore; manual_review=true",
        potential_priority_score=90,
        potential_priority="high",
        evidence_completeness_score=10,
        score_status="provisional",
        verified_total_score=None,
        verified_alert_tier="",
        enrichment_status="pending",
        enrichment_priority="high",
    )

    merged = merge_job(existing, incoming, seen_date="2026-06-23")

    assert merged.total_score == 88
    assert merged.alert_tier == "immediate_review"
    assert merged.score_status == "verified"
    assert merged.evidence_completeness_score == 90
    assert merged.verified_total_score == 88
    assert merged.enrichment_status == "enriched"


def test_unscored_duplicate_does_not_downgrade_existing_priority():
    existing = JobPosting(
        job_key="job-high-potential",
        company="Topgolf",
        title="Sr Manager, Strategic Planning",
        location="Dallas, TX",
        canonical_url="https://example.com/jobs/high-potential",
        total_score=21,
        alert_tier="ignore",
        potential_priority_score=78,
        potential_priority="high",
        potential_priority_reason="strategic senior role",
        evidence_completeness_score=10,
        score_status="provisional",
        enrichment_status="pending",
        enrichment_priority="high",
    )
    incoming = JobPosting(
        job_key="job-high-potential",
        company="Topgolf",
        title="Sr Manager, Strategic Planning",
        location="Dallas, TX",
        canonical_url="https://example.com/jobs/high-potential",
    )

    merged = merge_job(existing, incoming, seen_date="2026-06-23")

    assert merged.potential_priority_score == 78
    assert merged.potential_priority == "high"
    assert merged.potential_priority_reason == "strategic senior role"
    assert merged.score_status == "provisional"
    assert merged.evidence_completeness_score == 10
    assert merged.enrichment_status == "pending"
    assert merged.enrichment_priority == "high"


def test_sprint26_jobs_fields_are_appended_after_legacy_columns():
    assert JOB_FIELDS[34:36] == ["created_at", "updated_at"]
    assert JOB_FIELDS[36:] == [
        "potential_priority_score",
        "potential_priority",
        "potential_priority_reason",
        "evidence_completeness_score",
        "score_status",
        "verified_total_score",
        "verified_alert_tier",
        "enrichment_status",
        "enrichment_priority",
        "enrichment_last_attempted_at",
        "enrichment_completed_at",
        "enrichment_source_url",
        "enrichment_match_confidence",
    ]
