from __future__ import annotations

from src.enrichment.company_config import (
    company_config_from_row,
    load_company_configs,
    resolve_company_config,
)


class FakeSheetClient:
    def __init__(self, rows=None):
        self.rows = rows or []

    def read_records(self, worksheet_name):
        assert worksheet_name == "Config_Companies"
        return [dict(row) for row in self.rows]


def test_topgolf_and_toyota_defaults_have_official_company_search_paths():
    configs = load_company_configs(FakeSheetClient())

    topgolf = resolve_company_config("Topgolf Entertainment Group", configs)
    toyota = resolve_company_config("Toyota North America", configs)

    assert topgolf is not None
    assert topgolf.canonical_name == "Topgolf"
    assert topgolf.career_domain == "careers.topgolf.com"
    assert topgolf.career_search_url == "https://careers.topgolf.com/us/search-results"
    assert topgolf.ats_platform == "phenom"

    assert toyota is not None
    assert toyota.canonical_name == "Toyota Motor North America"
    assert toyota.career_domain == "careers.toyota.com"
    assert toyota.career_search_url == "https://careers.toyota.com/us/search-results"
    assert toyota.ats_platform == "phenom"


def test_sheet_configuration_supplements_default_without_erasing_missing_fields():
    configs = load_company_configs(
        FakeSheetClient(
            [
                {
                    "company_name": "Topgolf",
                    "ats_platform": "greenhouse",
                    "ats_board_token": "topgolf-test",
                    "enrichment_notes": "Fixture override",
                }
            ]
        )
    )

    topgolf = resolve_company_config("Top Golf USA", configs)

    assert topgolf is not None
    assert topgolf.ats_platform == "greenhouse"
    assert topgolf.board_token == "topgolf-test"
    assert topgolf.career_domain == "careers.topgolf.com"
    assert topgolf.enrichment_notes == "Fixture override"


def test_aliases_are_exact_and_do_not_fuzzy_merge_unrelated_legal_entities():
    config = company_config_from_row(
        {
            "company_name": "Example Holdings",
            "company_aliases": "Example Operating Company|Example Consumer",
            "enrichment_active": True,
        }
    )

    assert resolve_company_config("Example Operating Company", [config]) is config
    assert resolve_company_config("Example Operating", [config]) is None
    assert resolve_company_config("Different Example Company", [config]) is None


def test_inactive_configuration_is_not_resolved():
    config = company_config_from_row(
        {
            "company_name": "Inactive Company",
            "enrichment_active": "false",
            "ats_platform": "greenhouse",
            "ats_board_token": "inactive",
        }
    )

    assert resolve_company_config("Inactive Company", [config]) is None
