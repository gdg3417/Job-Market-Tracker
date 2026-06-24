from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Protocol
from urllib.parse import urlsplit

import requests

DEFAULT_USER_AGENT = "JobMarketTracker-Enrichment/1.0 (+direct-link-stage)"


class ResponseLike(Protocol):
    status_code: int
    url: str
    headers: dict[str, Any]
    encoding: str | None

    def iter_content(self, chunk_size: int = 65536) -> Iterable[bytes]:
        ...

    def close(self) -> None:
        ...


class SessionLike(Protocol):
    max_redirects: int

    def get(self, url: str, **kwargs: Any) -> ResponseLike:
        ...


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    timeout_seconds: int = 15
    max_redirects: int = 5
    max_response_bytes: int = 2_000_000
    minimum_domain_interval_seconds: float = 1.0
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "application/xhtml+xml",
        "application/ld+json",
    )


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


class EnrichmentFetchError(RuntimeError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        final_url: str = "",
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.status_code = status_code
        self.final_url = final_url


class DomainRateLimiter:
    def __init__(self, minimum_interval_seconds: float = 1.0) -> None:
        self.minimum_interval_seconds = max(0.0, float(minimum_interval_seconds))
        self._lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.minimum_interval_seconds <= 0:
            return
        domain = urlsplit(url).netloc.lower()
        if not domain:
            return
        with self._lock:
            now = time.monotonic()
            previous = self._last_request_at.get(domain)
            if previous is not None:
                remaining = self.minimum_interval_seconds - (now - previous)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_at[domain] = time.monotonic()


def _validate_url(url: str) -> str:
    candidate = str(url or "").strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise EnrichmentFetchError("invalid_url", f"Invalid enrichment URL: {candidate}", retryable=False) from exc
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise EnrichmentFetchError("invalid_url", f"Only HTTP and HTTPS enrichment URLs are supported: {candidate}", retryable=False)
    return candidate


def _content_type(headers: dict[str, Any]) -> str:
    raw = str(headers.get("Content-Type") or headers.get("content-type") or "").strip().lower()
    return raw.split(";", 1)[0].strip()


def _content_length(headers: dict[str, Any]) -> int | None:
    raw = headers.get("Content-Length") or headers.get("content-length")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _decode_body(body: bytes, encoding: str | None) -> str:
    preferred = str(encoding or "").strip() or "utf-8"
    try:
        return body.decode(preferred, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class DirectLinkFetcher:
    def __init__(
        self,
        *,
        session: SessionLike | None = None,
        policy: FetchPolicy | None = None,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self.policy = policy or FetchPolicy()
        self.session = session or requests.Session()
        self.session.max_redirects = self.policy.max_redirects
        self.rate_limiter = rate_limiter or DomainRateLimiter(self.policy.minimum_domain_interval_seconds)

    def fetch(self, url: str) -> FetchResult:
        requested_url = _validate_url(url)
        self.rate_limiter.wait(requested_url)
        response: ResponseLike | None = None
        try:
            response = self.session.get(
                requested_url,
                timeout=self.policy.timeout_seconds,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/ld+json"},
                allow_redirects=True,
                stream=True,
            )
            status_code = int(response.status_code)
            final_url = _validate_url(response.url or requested_url)

            if status_code in {404, 410}:
                raise EnrichmentFetchError(
                    "not_found",
                    f"Direct URL returned HTTP {status_code}",
                    retryable=False,
                    status_code=status_code,
                    final_url=final_url,
                )
            if status_code == 429 or status_code >= 500:
                raise EnrichmentFetchError(
                    "http_retryable",
                    f"Direct URL returned HTTP {status_code}",
                    retryable=True,
                    status_code=status_code,
                    final_url=final_url,
                )
            if status_code >= 400:
                raise EnrichmentFetchError(
                    "http_permanent",
                    f"Direct URL returned HTTP {status_code}",
                    retryable=False,
                    status_code=status_code,
                    final_url=final_url,
                )

            content_type = _content_type(response.headers)
            if content_type not in self.policy.allowed_content_types:
                raise EnrichmentFetchError(
                    "unsupported_content_type",
                    f"Unsupported enrichment content type: {content_type or 'missing'}",
                    retryable=False,
                    status_code=status_code,
                    final_url=final_url,
                )

            declared_length = _content_length(response.headers)
            if declared_length is not None and declared_length > self.policy.max_response_bytes:
                raise EnrichmentFetchError(
                    "response_too_large",
                    f"Response declares {declared_length} bytes, above the {self.policy.max_response_bytes} byte limit",
                    retryable=False,
                    status_code=status_code,
                    final_url=final_url,
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.policy.max_response_bytes:
                    raise EnrichmentFetchError(
                        "response_too_large",
                        f"Response exceeded the {self.policy.max_response_bytes} byte limit",
                        retryable=False,
                        status_code=status_code,
                        final_url=final_url,
                    )
                chunks.append(chunk)

            return FetchResult(
                requested_url=requested_url,
                final_url=final_url,
                status_code=status_code,
                content_type=content_type,
                text=_decode_body(b"".join(chunks), response.encoding),
            )
        except EnrichmentFetchError:
            raise
        except requests.TooManyRedirects as exc:
            raise EnrichmentFetchError("redirect_limit", "Direct URL exceeded the redirect limit", retryable=False) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise EnrichmentFetchError("network_retryable", str(exc), retryable=True) from exc
        except requests.RequestException as exc:
            raise EnrichmentFetchError("request_failure", str(exc), retryable=False) from exc
        finally:
            if response is not None:
                response.close()
