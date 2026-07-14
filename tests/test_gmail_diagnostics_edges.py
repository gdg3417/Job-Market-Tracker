from pathlib import Path
from types import SimpleNamespace

import pytest

from src.gmail_diagnostics import (
    GmailAuthenticationError,
    build_noninteractive_gmail_service,
    classify_failure,
)


class HttpError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status_code)


def test_gmail_rate_limit_403_is_retryable_api_failure():
    diagnostic = classify_failure(
        HttpError("userRateLimitExceeded: quota exceeded", 403),
        stage="list_messages",
    )

    assert diagnostic.category == "gmail_api"
    assert diagnostic.retry_eligible is True
    assert diagnostic.http_status == 403


def test_gmail_authentication_403_is_not_retryable():
    diagnostic = classify_failure(
        HttpError("insufficient authentication scopes", 403),
        stage="retrieve_message",
    )

    assert diagnostic.category == "authentication"
    assert diagnostic.retry_eligible is False


def test_generic_forbidden_403_remains_gmail_api_but_not_retryable():
    diagnostic = classify_failure(
        HttpError("forbidden", 403),
        stage="retrieve_message",
    )

    assert diagnostic.category == "gmail_api"
    assert diagnostic.retry_eligible is False


def test_missing_client_configuration_fails_without_interactive_authorization(tmp_path: Path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")

    with pytest.raises(GmailAuthenticationError, match="client configuration file was not found"):
        build_noninteractive_gmail_service(tmp_path / "missing-client.json", token_path)
