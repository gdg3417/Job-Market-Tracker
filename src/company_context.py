from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from src.models import normalize_key_part

COMPANY_CONTEXT_WORKSHEETS = ("Config_Companies", "Target_Companies")
COMPANY_NAME_FIELDS = ("company_name", "canonical_company_name", "parent_company")
COMPANY_ALIAS_FIELDS = ("company_aliases", "aliases")


def _alias_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[;,|\n]+", str(value)) if part.strip()]


def _context_names(row: dict[str, Any]) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    for field_name in COMPANY_NAME_FIELDS:
        value = str(row.get(field_name) or "").strip()
        if value:
            names.append((value, field_name))
    for field_name in COMPANY_ALIAS_FIELDS:
        names.extend((alias, "alias") for alias in _alias_values(row.get(field_name)))
    return names


def _canonical_name(row: dict[str, Any]) -> str:
    return str(
        row.get("canonical_company_name") or row.get("company_name") or row.get("parent_company") or ""
    ).strip()


def build_company_context_map(*row_groups: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Merge company configuration under canonical names, parents, and explicit aliases."""
    canonical_contexts: dict[str, dict[str, Any]] = {}
    name_to_canonical: dict[str, str] = {}
    name_metadata: dict[str, tuple[str, str]] = {}

    for rows in row_groups:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            canonical_name = _canonical_name(row)
            canonical_key = normalize_key_part(canonical_name)
            if not canonical_key:
                continue
            nonblank = {key: value for key, value in row.items() if value not in (None, "")}
            context = canonical_contexts.setdefault(canonical_key, {})
            context.update(nonblank)
            context["resolved_canonical_company_name"] = canonical_name

            for company_name, match_type in _context_names(row):
                company_key = normalize_key_part(company_name)
                if not company_key:
                    continue
                name_to_canonical[company_key] = canonical_key
                name_metadata[company_key] = (company_name, match_type)
            name_to_canonical.setdefault(canonical_key, canonical_key)
            name_metadata.setdefault(canonical_key, (canonical_name, "canonical_company_name"))

    contexts: dict[str, dict[str, Any]] = {}
    for company_key, canonical_key in name_to_canonical.items():
        context = dict(canonical_contexts.get(canonical_key, {}))
        matched_name, match_type = name_metadata.get(company_key, ("", ""))
        context["context_match_name"] = matched_name
        context["context_match_type"] = match_type
        contexts[company_key] = context
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
    context = context_map.get(normalize_key_part(company_name))
    return dict(context) if context else None
