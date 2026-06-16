from __future__ import annotations

from typing import Any


ACTIVE_TRUE_VALUES = {"1", "true", "yes", "y", "active"}


def is_active_company(row: dict[str, Any]) -> bool:
    return str(row.get("active", "")).strip().lower() in ACTIVE_TRUE_VALUES


def companies_by_source_type(rows: list[dict[str, Any]], source_type: str) -> list[dict[str, Any]]:
    source_type_lower = source_type.lower()
    return [row for row in rows if is_active_company(row) and str(row.get("source_type", "")).strip().lower() == source_type_lower]
