from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.resolution.scoring import ResolutionThresholds


@dataclass(frozen=True, slots=True)
class ResolutionSettings:
    thresholds: ResolutionThresholds = ResolutionThresholds()
    maximum_candidates_per_job: int = 20
    direct_url_budget: int = 8
    career_search_link_budget: int = 8
    ats_candidate_budget: int = 20
    search_query_budget: int = 3
    search_results_per_query: int = 5
    external_page_budget: int = 3
    timeout_seconds: int = 15

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "ResolutionSettings":
        matching = dict(values.get("matching") or {})
        limits = dict(values.get("limits") or {})
        return cls(
            thresholds=ResolutionThresholds(
                authoritative=int(matching.get("authoritative_threshold", 82)),
                probable=int(matching.get("probable_threshold", 70)),
                ambiguity_margin=int(matching.get("ambiguity_margin", 5)),
                minimum_company=int(matching.get("minimum_company_match", 75)),
                minimum_title=int(matching.get("minimum_title_match", 70)),
            ),
            maximum_candidates_per_job=max(1, int(limits.get("maximum_candidates_per_job", 20))),
            direct_url_budget=max(0, int(limits.get("direct_url_budget", 8))),
            career_search_link_budget=max(0, int(limits.get("career_search_link_budget", 8))),
            ats_candidate_budget=max(0, int(limits.get("ats_candidate_budget", 20))),
            search_query_budget=max(0, int(limits.get("search_query_budget", 3))),
            search_results_per_query=max(0, int(limits.get("search_results_per_query", 5))),
            external_page_budget=max(0, int(limits.get("external_page_budget", 3))),
            timeout_seconds=max(1, int(limits.get("timeout_seconds", 15))),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ResolutionSettings":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(yaml.safe_load(handle) or {})
