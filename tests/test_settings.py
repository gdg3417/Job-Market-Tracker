from src.settings import DEFAULT_GMAIL_SMOKE_MAX_RESULTS, load_settings


def test_gmail_max_results_defaults_to_smoke_cap(monkeypatch):
    monkeypatch.delenv("GMAIL_MAX_RESULTS", raising=False)

    settings = load_settings()

    assert settings.gmail_max_results == DEFAULT_GMAIL_SMOKE_MAX_RESULTS


def test_gmail_max_results_is_capped_for_smoke_safety(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "50")

    settings = load_settings()

    assert settings.gmail_max_results == DEFAULT_GMAIL_SMOKE_MAX_RESULTS


def test_gmail_max_results_has_minimum_one(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "0")

    settings = load_settings()

    assert settings.gmail_max_results == 1
