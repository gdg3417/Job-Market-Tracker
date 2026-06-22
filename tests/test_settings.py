from src.settings import DEFAULT_GMAIL_MAX_RESULTS, MAX_GMAIL_MAX_RESULTS, load_settings


def test_gmail_max_results_defaults_to_production_limit(monkeypatch):
    monkeypatch.delenv("GMAIL_MAX_RESULTS", raising=False)

    settings = load_settings()

    assert settings.gmail_max_results == DEFAULT_GMAIL_MAX_RESULTS == 50


def test_gmail_max_results_accepts_fifty(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "50")

    settings = load_settings()

    assert settings.gmail_max_results == 50


def test_gmail_max_results_can_be_lower_than_default(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "2")

    settings = load_settings()

    assert settings.gmail_max_results == 2


def test_gmail_max_results_has_minimum_one(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "0")

    settings = load_settings()

    assert settings.gmail_max_results == 1


def test_gmail_max_results_invalid_value_falls_back_safely(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "not-a-number")

    settings = load_settings()

    assert settings.gmail_max_results == DEFAULT_GMAIL_MAX_RESULTS


def test_gmail_max_results_clamps_to_supported_maximum(monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "9999")

    settings = load_settings()

    assert settings.gmail_max_results == MAX_GMAIL_MAX_RESULTS == 500
