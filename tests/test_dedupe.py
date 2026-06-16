from src.dedupe import find_duplicate, is_duplicate
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
