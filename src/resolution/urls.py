from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError, is_safe_public_url

TRACKING_PARAMS = {
    "trk",
    "trackingid",
    "tracking_id",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "source",
    "ref",
    "referrer",
    "gh_src",
    "lever-source",
    "campaign",
    "campaignid",
}
TRACKING_PREFIXES = ("utm_", "trk_")
WRAPPED_URL_PARAMS = (
    "url",
    "target",
    "redirect",
    "redirect_url",
    "redirecturi",
    "destination",
    "dest",
    "u",
    "q",
    "uddg",
)


@dataclass(frozen=True, slots=True)
class UrlResolution:
    observed_url: str
    unwrapped_url: str
    canonical_url: str
    redirect_count: int = 0
    status: str = "resolved"
    error_type: str = ""
    error_message: str = ""
    http_status: int | None = None


def _clean(value: Any) -> str:
    return html.unescape(str(value or "")).strip().strip("<>'\"")


def _query_pairs(parts) -> list[tuple[str, str]]:
    return list(parse_qsl(parts.query, keep_blank_values=True))


def unwrap_url(value: Any, *, max_depth: int = 5) -> str:
    current = _clean(value)
    if current.startswith("//"):
        current = f"https:{current}"
    elif re.match(r"^www\.", current, flags=re.IGNORECASE):
        current = f"https://{current}"
    for _ in range(max(0, max_depth)):
        try:
            parts = urlsplit(current)
        except ValueError:
            return ""
        values = {key.lower(): raw for key, raw in _query_pairs(parts)}
        nested = next((values.get(name) for name in WRAPPED_URL_PARAMS if values.get(name)), "")
        if not nested:
            break
        decoded = unquote(html.unescape(nested)).strip()
        if decoded.startswith("//"):
            decoded = f"https:{decoded}"
        elif decoded.startswith("/"):
            decoded = urljoin(current, decoded)
        if not is_safe_public_url(decoded) or decoded == current:
            break
        current = decoded
    return current if is_safe_public_url(current) else ""


def canonicalize_url(value: Any) -> str:
    unwrapped = unwrap_url(value)
    if not unwrapped:
        return ""
    try:
        parts = urlsplit(unwrapped)
    except ValueError:
        return ""
    kept: list[tuple[str, str]] = []
    for key, raw in _query_pairs(parts):
        normalized = key.lower()
        if normalized in TRACKING_PARAMS or any(normalized.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        kept.append((key, raw))
    query = urlencode(sorted(kept, key=lambda pair: (pair[0].lower(), pair[1])), doseq=True)
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    canonical = urlunsplit((parts.scheme.lower(), netloc, path, query, ""))
    return canonical if is_safe_public_url(canonical) else ""


def resolve_redirect_chain(value: Any, *, fetcher: DirectLinkFetcher | Any | None = None) -> UrlResolution:
    observed = _clean(value)
    unwrapped = unwrap_url(observed)
    if not unwrapped:
        return UrlResolution(observed, "", "", status="unsupported", error_type="unsafe_url", error_message="URL is not a safe public HTTP or HTTPS destination")
    client = fetcher or DirectLinkFetcher()
    try:
        result = client.fetch(unwrapped)
    except EnrichmentFetchError as exc:
        status = "retryable_failure" if exc.retryable else "blocked" if exc.error_type in {"access_blocked", "unsafe_url"} else "not_found"
        final = canonicalize_url(exc.final_url or unwrapped)
        return UrlResolution(
            observed,
            unwrapped,
            final,
            status=status,
            error_type=exc.error_type,
            error_message=str(exc),
            http_status=exc.status_code,
        )
    return UrlResolution(
        observed,
        unwrapped,
        canonicalize_url(result.final_url or unwrapped),
        status="resolved",
        http_status=result.status_code,
    )
