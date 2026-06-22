from pathlib import Path

import pytest

from src.normalize import normalize_raw_job
from src.scoring import load_scoring_rules, score_job


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


def _sparse_job(title: str, *, source_primary: str):
    return normalize_raw_job(
        {
            "company": "Acme Industrial",
            "title": title,
            "location": "Dallas, TX",
            "source_primary": source_primary,
            "description": (
                "Extracted from Gmail job alert. confidence=high. "
                "origin=linkedin; extraction=linkedin_digest_card; linkedin_job_id=1234567890"
            ),
        },
        source_primary=source_primary,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Chief of Staff to the CEO",
        "Director, Strategy & Operations",
    ],
)
def test_sparse_high_signal_title_variants_receive_score_neutral_review_marker(title: str):
    rules = load_scoring_rules(RULES_PATH)
    gmail_job = score_job(_sparse_job(title, source_primary="gmail_alert"), rules)
    baseline_job = score_job(_sparse_job(title, source_primary="manual"), rules)

    assert gmail_job.total_score == baseline_job.total_score
    assert gmail_job.alert_tier == baseline_job.alert_tier
    assert "manual_review=true" in gmail_job.score_explanation
    assert "review_reason=sparse_gmail_high_signal_title" in gmail_job.score_explanation
    assert "manual_review=true" not in baseline_job.score_explanation
