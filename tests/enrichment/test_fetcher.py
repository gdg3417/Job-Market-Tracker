from __future__ import annotations

import pytest

from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError, FetchPolicy


class FakeResponse:
    def __init__(self, url: str, body: bytes, status: int = 200, content_type: str = "text/html", content_length: int | None = None):
        self.url = url
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.max_redirects = 0

    def get(self, url: str, **kwargs):
        return self.response


def build_fetcher(response: FakeResponse, max_bytes: int = 1000) -> DirectLinkFetcher:
    return DirectLinkFetcher(
        session=FakeSession(response),
        policy=FetchPolicy(max_response_bytes=max_bytes, minimum_domain_interval_seconds=0),
    )


def test_redirected_url_uses_final_url():
    response = FakeResponse("https://careers.example.com/jobs/123", b"<html></html>")
    result = build_fetcher(response).fetch("https://tracking.example.com/click/abc")
    assert result.final_url == "https://careers.example.com/jobs/123"
    assert response.closed is True


def test_unsupported_content_type_fails_safely():
    response = FakeResponse("https://example.com/jobs/1", b"data", content_type="application/octet-stream")
    with pytest.raises(EnrichmentFetchError) as caught:
        build_fetcher(response).fetch("https://example.com/jobs/1")
    assert caught.value.error_type == "unsupported_content_type"
    assert caught.value.retryable is False


def test_declared_oversized_response_fails_safely():
    response = FakeResponse("https://example.com/jobs/1", b"small", content_length=5000)
    with pytest.raises(EnrichmentFetchError) as caught:
        build_fetcher(response, max_bytes=100).fetch("https://example.com/jobs/1")
    assert caught.value.error_type == "response_too_large"


def test_streamed_oversized_response_fails_safely():
    response = FakeResponse("https://example.com/jobs/1", b"x" * 101)
    with pytest.raises(EnrichmentFetchError) as caught:
        build_fetcher(response, max_bytes=100).fetch("https://example.com/jobs/1")
    assert caught.value.error_type == "response_too_large"
