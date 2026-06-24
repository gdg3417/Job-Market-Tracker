from __future__ import annotations

import pytest

from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError, FetchPolicy


class FakeResponse:
    def __init__(
        self,
        url: str,
        body: bytes,
        status: int = 200,
        content_type: str = "text/html",
        content_length: int | None = None,
        location: str = "",
    ):
        self.url = url
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        if location:
            self.headers["Location"] = location
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses: FakeResponse | list[FakeResponse]):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.calls = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def build_fetcher(responses: FakeResponse | list[FakeResponse], max_bytes: int = 1000) -> DirectLinkFetcher:
    return DirectLinkFetcher(
        session=FakeSession(responses),
        policy=FetchPolicy(max_response_bytes=max_bytes, minimum_domain_interval_seconds=0),
    )


def test_redirected_url_is_validated_and_resolved_manually():
    redirect = FakeResponse(
        "https://tracking.example.com/click/abc",
        b"",
        status=302,
        location="https://careers.example.com/jobs/123",
    )
    final = FakeResponse("https://careers.example.com/jobs/123", b"<html></html>")
    fetcher = build_fetcher([redirect, final])

    result = fetcher.fetch("https://tracking.example.com/click/abc")

    assert result.final_url == "https://careers.example.com/jobs/123"
    assert redirect.closed is True
    assert final.closed is True
    assert [call[0] for call in fetcher.session.calls] == [
        "https://tracking.example.com/click/abc",
        "https://careers.example.com/jobs/123",
    ]
    assert all(call[1]["allow_redirects"] is False for call in fetcher.session.calls)


def test_private_ip_url_is_rejected_before_request():
    response = FakeResponse("https://example.com/jobs/1", b"unused")
    fetcher = build_fetcher(response)

    with pytest.raises(EnrichmentFetchError) as caught:
        fetcher.fetch("http://169.254.169.254/latest/meta-data")

    assert caught.value.error_type == "unsafe_url"
    assert fetcher.session.calls == []


def test_redirect_to_private_ip_is_rejected_before_second_request():
    redirect = FakeResponse(
        "https://tracking.example.com/click/abc",
        b"",
        status=302,
        location="http://127.0.0.1/private",
    )
    fetcher = build_fetcher(redirect)

    with pytest.raises(EnrichmentFetchError) as caught:
        fetcher.fetch("https://tracking.example.com/click/abc")

    assert caught.value.error_type == "unsafe_url"
    assert len(fetcher.session.calls) == 1
    assert redirect.closed is True


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
