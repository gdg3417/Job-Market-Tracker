from __future__ import annotations

from src.schema import CONFIG_COMPANIES_HEADERS


SPRINT_28_COMPANY_HEADERS = [
    "canonical_company_name",
    "company_aliases",
    "career_domain",
    "career_search_url",
    "ats_company_id",
    "ats_board_token",
    "enrichment_mode",
    "enrichment_active",
    "enrichment_notes",
]


def test_sprint_28_company_configuration_fields_are_trailing_migration_columns():
    assert CONFIG_COMPANIES_HEADERS[-len(SPRINT_28_COMPANY_HEADERS) :] == SPRINT_28_COMPANY_HEADERS
    assert len(CONFIG_COMPANIES_HEADERS) == len(set(CONFIG_COMPANIES_HEADERS))
