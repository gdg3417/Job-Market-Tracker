from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

ENRICHMENT_PLATFORMS = {
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "smart recruiters",
    "workday",
    "icims",
    "successfactors",
    "success factors",
    "phenom",
    "oracle",
    "oracle recruiting",
    "company_api",
    "company-specific",
}

DEFAULT_COMPANY_ROWS: tuple[dict[str, Any], ...] = (
    {
        "company_id": "topgolf",
        "company_name": "Topgolf",
        "canonical_company_name": "Topgolf",
        "company_aliases": "Topgolf Entertainment Group|Top Golf USA|Topgolf Callaway Brands",
        "career_domain": "careers.topgolf.com",
        "career_search_url": "https://careers.topgolf.com/us/search-results",
        "ats_platform": "phenom",
        "enrichment_mode": "configured_career_search",
        "enrichment_active": True,
        "enrichment_notes": "Official Topgolf Phenom career site. Use configured endpoints only; do not scrape the landing page as a posting.",
    },
    {
        "company_id": "toyota-motor-north-america",
        "company_name": "Toyota Motor North America",
        "canonical_company_name": "Toyota Motor North America",
        "company_aliases": "Toyota North America|Toyota|Toyota Financial Services",
        "career_domain": "careers.toyota.com",
        "career_search_url": "https://careers.toyota.com/us/search-results",
        "ats_platform": "phenom",
        "enrichment_mode": "configured_career_search",
        "enrichment_active": True,
        "enrichment_notes": "Official Toyota North America Phenom career site. Aliases identify the candidate source but do not bypass posting-level match validation.",
    },
)


def normalize_company_name(value: Any) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _normalize_platform(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _split_aliases(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        parts = [str(item or "").strip() for item in value]
    else:
        parts = re.split(r"[|;\n]+", str(value or ""))
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        clean = str(part or "").strip()
        normalized = normalize_company_name(clean)
        if not clean or not normalized or normalized in seen:
            continue
        unique.append(clean)
        seen.add(normalized)
    return tuple(unique)


def _domain_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text.strip("/").lower()
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def _implicit_enrichment_active(row: dict[str, Any], career_search_url: str) -> bool:
    platform = _normalize_platform(row.get("ats_platform") or row.get("source_type"))
    identifier = str(row.get("ats_board_token") or row.get("ats_company_id") or row.get("source_slug") or "").strip()
    return platform in ENRICHMENT_PLATFORMS and bool(career_search_url or identifier)


@dataclass(frozen=True, slots=True)
class CompanyEnrichmentConfig:
    company_id: str = ""
    company_name: str = ""
    canonical_company_name: str = ""
    company_aliases: tuple[str, ...] = ()
    parent_company: str = ""
    career_domain: str = ""
    career_search_url: str = ""
    ats_platform: str = ""
    ats_company_id: str = ""
    ats_board_token: str = ""
    source_slug: str = ""
    source_url: str = ""
    enrichment_mode: str = ""
    enrichment_active: bool = True
    enrichment_notes: str = ""

    @property
    def canonical_name(self) -> str:
        return self.canonical_company_name or self.company_name

    @property
    def normalized_names(self) -> tuple[str, ...]:
        names = [self.canonical_name, self.company_name, *self.company_aliases]
        normalized: list[str] = []
        seen: set[str] = set()
        for name in names:
            value = normalize_company_name(name)
            if value and value not in seen:
                normalized.append(value)
                seen.add(value)
        return tuple(normalized)

    @property
    def board_token(self) -> str:
        return self.ats_board_token or self.source_slug

    @property
    def company_identifier(self) -> str:
        return self.ats_company_id or self.source_slug

    def to_company_row(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "company_name": self.canonical_name,
            "canonical_company_name": self.canonical_name,
            "company_aliases": "|".join(self.company_aliases),
            "parent_company": self.parent_company,
            "career_domain": self.career_domain,
            "career_search_url": self.career_search_url,
            "ats_platform": self.ats_platform,
            "ats_company_id": self.ats_company_id,
            "ats_board_token": self.ats_board_token,
            "source_slug": self.board_token,
            "source_url": self.source_url or self.career_search_url,
            "enrichment_mode": self.enrichment_mode,
            "enrichment_active": self.enrichment_active,
            "active": self.enrichment_active,
            "enrichment_notes": self.enrichment_notes,
        }


def company_config_from_row(row: dict[str, Any]) -> CompanyEnrichmentConfig:
    company_name = str(row.get("company_name") or "").strip()
    canonical_name = str(row.get("canonical_company_name") or company_name).strip()
    career_search_url = str(row.get("career_search_url") or row.get("source_url") or "").strip()
    career_domain = str(row.get("career_domain") or _domain_from_url(career_search_url)).strip().lower()
    active_value = row.get("enrichment_active")
    if active_value in (None, ""):
        active_value = _implicit_enrichment_active(row, career_search_url)
    return CompanyEnrichmentConfig(
        company_id=str(row.get("company_id") or "").strip(),
        company_name=company_name or canonical_name,
        canonical_company_name=canonical_name or company_name,
        company_aliases=_split_aliases(row.get("company_aliases")),
        parent_company=str(row.get("parent_company") or "").strip(),
        career_domain=career_domain,
        career_search_url=career_search_url,
        ats_platform=str(row.get("ats_platform") or row.get("source_type") or "").strip().lower(),
        ats_company_id=str(row.get("ats_company_id") or "").strip(),
        ats_board_token=str(row.get("ats_board_token") or "").strip(),
        source_slug=str(row.get("source_slug") or "").strip(),
        source_url=str(row.get("source_url") or "").strip(),
        enrichment_mode=str(row.get("enrichment_mode") or row.get("ingestion_mode") or "").strip().lower(),
        enrichment_active=_truthy(active_value, default=False),
        enrichment_notes=str(row.get("enrichment_notes") or row.get("notes") or "").strip(),
    )


def _nonempty_values(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}


def _primary_identity(row: dict[str, Any]) -> set[str]:
    names = [row.get("canonical_company_name"), row.get("company_name")]
    return {normalize_company_name(name) for name in names if normalize_company_name(name)}


def merge_company_rows(default_rows: Iterable[dict[str, Any]], sheet_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    combined = [dict(row) for row in default_rows]
    for sheet_row in sheet_rows:
        row = dict(sheet_row)
        identity = _primary_identity(row)
        match_index = next((index for index, current in enumerate(combined) if identity.intersection(_primary_identity(current))), None)
        if match_index is None:
            combined.append(row)
            continue
        merged = dict(combined[match_index])
        merged.update(_nonempty_values(row))
        combined[match_index] = merged
    return combined


def load_company_configs(sheet_client: Any, *, include_defaults: bool = True) -> list[CompanyEnrichmentConfig]:
    try:
        sheet_rows = list(sheet_client.read_records("Config_Companies"))
    except Exception:
        sheet_rows = []
    rows = merge_company_rows(DEFAULT_COMPANY_ROWS if include_defaults else (), sheet_rows)
    return [config for row in rows if (config := company_config_from_row(row)).canonical_name]


def resolve_company_config(
    company: Any,
    configs: Iterable[CompanyEnrichmentConfig],
) -> CompanyEnrichmentConfig | None:
    expected = normalize_company_name(company)
    if not expected:
        return None
    configs_list = [config for config in configs if config.enrichment_active]

    primary_matches = [
        config
        for config in configs_list
        if expected in {normalize_company_name(config.canonical_name), normalize_company_name(config.company_name)}
    ]
    if len(primary_matches) == 1:
        return primary_matches[0]
    if len(primary_matches) > 1:
        return None

    alias_matches = [config for config in configs_list if expected in {normalize_company_name(alias) for alias in config.company_aliases}]
    return alias_matches[0] if len(alias_matches) == 1 else None
