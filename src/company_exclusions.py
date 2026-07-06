from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "lp",
    "llp",
    "ltd",
    "limited",
    "plc",
    "corp",
    "corporation",
}
DEFAULT_REASON_CODE = "blocked_company"


@dataclass(frozen=True, slots=True)
class CompanyExclusionMatch:
    blocked: bool = False
    canonical_name: str = ""
    matched_alias: str = ""
    reason_code: str = ""
    category: str = ""


def load_company_exclusions(path: str | Path) -> dict[str, Any]:
    exclusions_path = Path(path)
    with exclusions_path.open("r", encoding="utf-8") as file:
        values = yaml.safe_load(file) or {}
    config = values.get("company_exclusions", values)
    if not isinstance(config, dict):
        return {"blocked_companies": []}
    config.setdefault("blocked_companies", [])
    config.setdefault("reason_code", DEFAULT_REASON_CODE)
    return config


def normalize_company_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    parts = [part for part in text.split() if part]
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _company_keys(value: Any) -> set[str]:
    normalized = normalize_company_name(value)
    if not normalized:
        return set()
    return {normalized, normalized.replace(" ", "")}


def _configured_aliases(entry: dict[str, Any]) -> list[str]:
    aliases = [str(entry.get("canonical_name") or "")]
    aliases.extend(str(value or "") for value in entry.get("aliases") or [])
    return [alias for alias in aliases if alias.strip()]


def evaluate_company_exclusion(company: Any, config: dict[str, Any] | None) -> CompanyExclusionMatch:
    company_keys = _company_keys(company)
    if not company_keys:
        return CompanyExclusionMatch()

    exclusions = (config or {}).get("blocked_companies") or []
    default_reason = str((config or {}).get("reason_code") or DEFAULT_REASON_CODE)
    for entry in exclusions:
        if not isinstance(entry, dict):
            continue
        for alias in _configured_aliases(entry):
            alias_keys = _company_keys(alias)
            if company_keys.intersection(alias_keys):
                canonical_name = str(entry.get("canonical_name") or alias).strip()
                return CompanyExclusionMatch(
                    blocked=True,
                    canonical_name=canonical_name,
                    matched_alias=str(alias).strip(),
                    reason_code=str(entry.get("reason_code") or default_reason),
                    category=str(entry.get("category") or "blocked_company"),
                )
    return CompanyExclusionMatch()
