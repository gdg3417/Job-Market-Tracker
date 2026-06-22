from pathlib import Path

import yaml


def test_sparse_gmail_review_configuration_has_required_signals():
    path = Path(__file__).resolve().parents[1] / "config" / "sparse_gmail_review.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["sparse_gmail_review"]
    assert config["review_reason"] == "sparse_gmail_high_signal_title"
    assert "Strategic Planning" in config["priority_title_phrases"]
    assert "National Manager" in config["priority_title_phrases"]
    assert "Manager" in config["seniority_phrases"]
