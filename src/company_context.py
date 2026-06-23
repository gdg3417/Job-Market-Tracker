from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.models import normalize_key_part

COMPANY_CONTEXT_WORKSHEETS = ("Config_Companies", "Target_Companies")
COMPANY_NAME_FIELDS = ("company_name", "parent_company")


def build_company_context_map(*row_groups: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Merge nonblank company configuration fields under normalized company and parent names."""
    contexts: dict[str, dict[str, Any]] = {}
    for rows in row_groups:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            nonblank = {key: value for key, value in row.items() if value not in (None, "")}
            for field_name in COMPANY_NAME_FIELDS:
                company_key = normalize_key_part(row.get(field_name))
                if not company_key:
                    continue
                contexts.setdefault(company_key, {}).update(nonblank)
    return contexts


def load_company_context_map(sheet_client: Any) -> dict[str, dict[str, Any]]:
    row_groups: list[list[dict[str, Any]]] = []
    if not hasattr(sheet_client, "read_records"):
        return {}
    for worksheet_name in COMPANY_CONTEXT_WORKSHEETS:
        try:
            row_groups.append(list(sheet_client.read_records(worksheet_name)))
        except Exception as exc:
            if exc.__class__.__name__ == "WorksheetNotFound":
                row_groups.append([])
                continue
            raise
    return build_company_context_map(*row_groups)


def company_context_for_name(
    company_name: str,
    context_map: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not context_map:
        return None
    return context_map.get(normalize_key_part(company_name))
