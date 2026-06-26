from src.dedupe import find_duplicate, is_duplicate, merge_job
from src.models import JobPosting
from src.normalize import normalize_raw_job


def test_duplicate_by_url():
    existing = [
        normalize_raw_job(
            {
                "company": "Acme",
                "title": "Director, Revenue Strategy",
                "location": "Dallas, TX",
                "url": "https://example.com/job/1",
            }
        )
    ]
    new_job = normalize_raw_job(
        {
            "company": "Acme Inc.",
            "title": "Director Revenue Strategy",
            "location": "Dallas",
            "url": "https://example.com/job/1",
        }
    )
    assert is_duplicate(new_job, existing)
    assert find_duplicate(new_job, existing) is existing[0]


def test_duplicate_by_fuzzy_identity():
    existing = [normalize_raw_job({"company": "Acme", "title": "Senior Manager, Commercial Strategy", "location": "Plano, TX"})]
    new_job = normalize_raw_job({"company": "Acme", "title": "Sr Manager Commercial Strategy", "location": "Plano Texas"})
    assert is_duplicate(new_job, existing, threshold=80)


def test_merge_preserves_stronger_existing_record():
    existing = JobPosting(
        job_key="record-1",
        company="Acme",
        title="Strategy Manager",
        location="Dallas, TX",
        source_primary="gmail_alert",
        description_text="Detailed recovered record.",
        remote_status="on-site",
        work_model="on-site",
        total_score=70,
        alert_tier="review",
        score_status="partially_verified",
        evidence_completeness_score=80,
        enrichment_status="enriched",
    )
    incoming = JobPosting(
        job_key="record-1",
        company="Acme",
        title="Strategy Manager",
        location="Dallas, TX",
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert.",
        remote_status="unknown",
        work_model="unknown",
        total_score=10,
        alert_tier="ignore",
        score_status="provisional",
        evidence_completeness_score=10,
        enrichment_status="pending",
    )

    merged = merge_job(existing, incoming, seen_date="2026-06-26")

    assert merged.description_text == "Detailed recovered record."
    assert merged.remote_status == "on-site"
    assert merged.work_model == "on-site"
    assert merged.total_score == 70
    assert merged.score_status == "partially_verified"
    assert merged.evidence_completeness_score == 80
    assert merged.enrichment_status == "enriched"


def test_merge_allows_stronger_incoming_record_to_upgrade_sparse_existing():
    existing = JobPosting(
        job_key="record-2",
        company="Acme",
        title="Strategy Manager",
        location="Dallas, TX",
        source_primary="gmail_alert",
        description_text="Extracted from Gmail job alert.",
        remote_status="unknown",
        work_model="unknown",
        total_score=10,
        alert_tier="ignore",
        score_status="provisional",
        evidence_completeness_score=10,
        enrichment_status="pending",
    )
    incoming = JobPosting(
        job_key="record-2",
        company="Acme",
        title="Strategy Manager",
        location="Dallas, TX",
        source_primary="greenhouse",
        description_text="Detailed authoritative record with operating ownership.",
        remote_status="hybrid",
        work_model="hybrid",
        total_score=72,
        alert_tier="review",
        score_status="partially_verified",
        evidence_completeness_score=80,
        enrichment_status="enriched",
    )

    merged = merge_job(existing, incoming, seen_date="2026-06-26")

    assert merged.description_text == incoming.description_text
    assert merged.remote_status == "hybrid"
    assert merged.work_model == "hybrid"
    assert merged.total_score == 72
    assert merged.score_status == "partially_verified"
    assert merged.evidence_completeness_score == 80
    assert merged.enrichment_status == "enriched"
