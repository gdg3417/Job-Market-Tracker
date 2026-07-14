from __future__ import annotations

from typing import Any, Iterable

from src.normalize import clean_text, normalize_url
from src.sources.static_pages import static_page_company_rows


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def configured_static_source_rows_for_audit(
    company_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return active configured static sources, including execution-blocked rows.

    Failed and manual-review static configurations must remain visible to the source
    audit without becoming eligible for normal ingestion. Gmail-only and explicitly
    disabled configurations remain outside the static-source audit.
    """
    candidates: list[dict[str, Any]] = []
    for raw in company_rows:
        row = dict(raw)
        if not _truthy(row.get("active"), default=True):
            continue
        if not normalize_url(row.get("source_url")):
            continue
        mode = clean_text(row.get("ingestion_mode")).strip().lower().replace("-", "_").replace(" ", "_")
        if mode in {"gmail_only", "disabled"}:
            continue
        row["ingestion_mode"] = "static_direct"
        row["source_quality"] = "success"
        candidates.append(row)
    return static_page_company_rows(candidates)
