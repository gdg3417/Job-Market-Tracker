from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from src.connectors.ats import connector_scope, normalize_platform
from src.enrichment.company_config import CompanyEnrichmentConfig, load_company_configs, resolve_company_config
from src.models import JobPosting
from src.resolution.models import PostingResolution
from src.source_reliability import SourceHealthState, platform_health_metrics, read_source_health

HIGH_POTENTIAL_VALUES = {"high"}
UNRESOLVED_STATES = {"", "not_found", "blocked", "unsupported", "retryable_failure", "resolved_probable", "ambiguous"}


@dataclass(slots=True)
class PlatformInventoryRow:
    platform: str
    connector_scope: str
    priority_company_count: int = 0
    tier_1_company_count: int = 0
    tier_2_company_count: int = 0
    unresolved_high_potential_jobs: int = 0
    active_config_count: int = 0
    invalid_configuration_count: int = 0
    watch_or_paused_sources: int = 0
    expected_implementation_value: int = 0
    companies: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _records(sheet_client: Any, worksheet: str) -> list[tuple[int, dict[str, Any]]]:
    try:
        if hasattr(sheet_client, "read_records_with_row_numbers"):
            return list(sheet_client.read_records_with_row_numbers(worksheet))
        return [(index + 2, row) for index, row in enumerate(sheet_client.read_records(worksheet))]
    except Exception as exc:
        if exc.__class__.__name__ in {"WorksheetNotFound", "KeyError"}:
            return []
        return []


def _jobs(sheet_client: Any) -> list[JobPosting]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return [job for _, job in sheet_client.read_jobs_with_row_numbers()]
    return [JobPosting.from_dict(row) for _, row in _records(sheet_client, "Jobs")]


def _target_tiers(sheet_client: Any) -> dict[str, str]:
    tiers: dict[str, str] = {}
    for _, row in _records(sheet_client, "Target_Companies"):
        if not _truthy(row.get("active"), default=True):
            continue
        name = str(row.get("company_name") or "").strip().lower()
        parent = str(row.get("parent_company") or "").strip().lower()
        tier = str(row.get("priority_tier") or "").strip().lower()
        for value in (name, parent):
            if value:
                tiers[value] = tier
    return tiers


def _is_priority_config(config: CompanyEnrichmentConfig, tiers: dict[str, str]) -> tuple[bool, str]:
    names = [config.company_name, config.canonical_name, *config.company_aliases]
    for name in names:
        normalized = str(name or "").strip().lower()
        if normalized in tiers:
            tier = tiers[normalized]
            return tier in {"1", "tier 1", "tier_1", "2", "tier 2", "tier_2"}, tier
    text = " ".join([config.enrichment_notes, config.company_name, config.canonical_name]).lower()
    if "tier 1" in text:
        return True, "tier 1"
    if "tier 2" in text:
        return True, "tier 2"
    return False, ""


def _resolution_states(sheet_client: Any) -> dict[str, str]:
    states: dict[str, str] = {}
    for _, row in _records(sheet_client, "Posting_Resolution"):
        resolution = PostingResolution.from_dict(row)
        if resolution.job_key:
            states[resolution.job_key] = resolution.resolution_state
    return states


def _unresolved_high_potential_by_platform(
    jobs: list[JobPosting],
    configs: list[CompanyEnrichmentConfig],
    resolution_states: dict[str, str],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for job in jobs:
        if job.status not in {"open", "reopened"}:
            continue
        if str(job.potential_priority or "").strip().lower() not in HIGH_POTENTIAL_VALUES:
            continue
        state = resolution_states.get(job.job_key, "")
        if state not in UNRESOLVED_STATES:
            continue
        config = resolve_company_config(job.company, configs)
        platform = normalize_platform(config.ats_platform) if config else "unconfigured"
        counts[platform or "unconfigured"] += 1
    return counts


def build_platform_inventory(sheet_client: Any) -> dict[str, Any]:
    configs = load_company_configs(sheet_client)
    tiers = _target_tiers(sheet_client)
    jobs = _jobs(sheet_client)
    resolution_states = _resolution_states(sheet_client)
    unresolved_counts = _unresolved_high_potential_by_platform(jobs, configs, resolution_states)
    health_index = read_source_health(sheet_client)
    health_states = [state for _, state in health_index.values()]
    health_by_platform = platform_health_metrics(health_states)

    rows_by_platform: dict[str, PlatformInventoryRow] = {}
    for config in configs:
        if not config.enrichment_active:
            continue
        platform = normalize_platform(config.ats_platform) or "unconfigured"
        row = rows_by_platform.setdefault(
            platform,
            PlatformInventoryRow(platform=platform, connector_scope=connector_scope(platform)),
        )
        row.active_config_count += 1
        is_priority, tier = _is_priority_config(config, tiers)
        if is_priority:
            row.priority_company_count += 1
            if tier in {"1", "tier 1", "tier_1"}:
                row.tier_1_company_count += 1
            elif tier in {"2", "tier 2", "tier_2"}:
                row.tier_2_company_count += 1
            if config.canonical_name not in row.companies:
                row.companies.append(config.canonical_name)

    for platform, count in unresolved_counts.items():
        row = rows_by_platform.setdefault(
            platform,
            PlatformInventoryRow(platform=platform, connector_scope=connector_scope(platform)),
        )
        row.unresolved_high_potential_jobs = count

    for platform, metrics in health_by_platform.items():
        row = rows_by_platform.setdefault(
            platform,
            PlatformInventoryRow(platform=platform, connector_scope=connector_scope(platform)),
        )
        row.invalid_configuration_count = int(metrics.get("invalid_configurations") or 0)
        row.watch_or_paused_sources = int(metrics.get("paused_sources") or 0)
        if metrics.get("failures_by_category"):
            row.notes = f"Recent failure categories: {json.dumps(metrics['failures_by_category'], sort_keys=True)}"

    for row in rows_by_platform.values():
        reliability_value = 20 if row.connector_scope == "structured" else 8 if row.connector_scope == "configured_only" else 0
        row.expected_implementation_value = (
            row.priority_company_count * 5
            + row.unresolved_high_potential_jobs * 4
            + row.active_config_count
            + reliability_value
            - row.invalid_configuration_count * 3
            - row.watch_or_paused_sources * 2
        )
        row.companies.sort()

    ranked = sorted(rows_by_platform.values(), key=lambda item: item.expected_implementation_value, reverse=True)
    selected_scope = [row.platform for row in ranked if row.connector_scope == "structured"]
    return {
        "platforms_ranked": [row.to_dict() for row in ranked],
        "selected_connector_scope": selected_scope,
        "platform_health": health_by_platform,
        "notes": (
            "Sprint 35 implements and formalizes the structured connector scope for Greenhouse, Lever, Ashby, "
            "and SmartRecruiters. Configured-only platforms remain inventoried and reviewable without generic crawling."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory priority ATS platforms and source reliability")
    parser.add_argument("--dry-run", action="store_true", help="Print inventory without writing workbook state")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from src.schema import validate_workbook_or_raise
    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    validate_workbook_or_raise(sheet_client)
    inventory = build_platform_inventory(sheet_client)
    print(json.dumps({"status": "success", "dry_run": bool(args.dry_run), **inventory}, indent=2))


if __name__ == "__main__":
    main()
