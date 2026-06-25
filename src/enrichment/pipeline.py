from __future__ import annotations

import argparse
import json
from typing import Any

from src.enrichment.company_run import preview_company_ats_queue, run_company_ats_enrichment
from src.enrichment.run import preview_direct_link_queue, run_direct_link_enrichment
from src.enrichment.search import (
    DEFAULT_CANDIDATE_PAGE_BUDGET,
    DEFAULT_QUERY_BUDGET,
    DEFAULT_RESULTS_PER_QUERY,
    DisabledSearchProvider,
    DuckDuckGoHtmlSearchProvider,
    SearchProvider,
)
from src.enrichment.search_run import preview_external_search_queue, run_external_search_enrichment


def run_enrichment_pipeline(
    sheet_client: Any,
    *,
    direct_limit: int = 10,
    company_limit: int = 10,
    external_limit: int = 10,
    job_key: str = "",
    replay: bool = False,
    search_provider: SearchProvider | None = None,
    search_query_budget: int = DEFAULT_QUERY_BUDGET,
    search_results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    candidate_page_budget: int = DEFAULT_CANDIDATE_PAGE_BUDGET,
) -> dict[str, Any]:
    direct = run_direct_link_enrichment(
        sheet_client,
        limit=direct_limit,
        job_key=job_key,
        replay=replay,
    )
    company = run_company_ats_enrichment(
        sheet_client,
        limit=company_limit,
        job_key=job_key,
    )
    external = run_external_search_enrichment(
        sheet_client,
        limit=external_limit,
        job_key=job_key,
        provider=search_provider,
        query_budget=search_query_budget,
        results_per_query=search_results_per_query,
        candidate_page_budget=candidate_page_budget,
    )
    return {
        "direct_link": direct.to_dict(),
        "company_ats": company.to_dict(),
        "external_search": external.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run direct-link, company and ATS, then external-search enrichment"
    )
    parser.add_argument("--run", action="store_true", help="Run all enrichment stages")
    parser.add_argument("--dry-run", action="store_true", help="Preview currently eligible work without writes")
    parser.add_argument("--direct-limit", type=int, default=10, help="Maximum direct URLs to process")
    parser.add_argument("--company-limit", type=int, default=10, help="Maximum company and ATS items to process")
    parser.add_argument("--external-limit", type=int, default=10, help="Maximum external-search items to process")
    parser.add_argument("--search-query-budget", type=int, default=DEFAULT_QUERY_BUDGET, help="Maximum search queries per job")
    parser.add_argument("--search-results-per-query", type=int, default=DEFAULT_RESULTS_PER_QUERY, help="Maximum search results retained per query")
    parser.add_argument("--candidate-page-budget", type=int, default=DEFAULT_CANDIDATE_PAGE_BUDGET, help="Maximum authoritative candidate pages fetched per job")
    parser.add_argument("--no-web-search", action="store_true", help="Generate manual review links without querying DuckDuckGo")
    parser.add_argument("--job-key", default="", help="Restrict all stages to one existing Jobs job_key")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay one terminal direct-link queue item before later stages; requires --run and --job-key",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.run and not args.dry_run:
        raise SystemExit("Choose --run or --dry-run")
    if args.replay and (not args.run or not args.job_key):
        raise SystemExit("--replay requires --run and an exact --job-key")

    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        print(
            json.dumps(
                {
                    "direct_link": preview_direct_link_queue(sheet_client, job_key=args.job_key),
                    "company_ats": preview_company_ats_queue(sheet_client, job_key=args.job_key),
                    "external_search": preview_external_search_queue(sheet_client, job_key=args.job_key),
                },
                indent=2,
            )
        )
        return

    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    provider: SearchProvider = DisabledSearchProvider() if args.no_web_search else DuckDuckGoHtmlSearchProvider()
    print(
        json.dumps(
            run_enrichment_pipeline(
                sheet_client,
                direct_limit=args.direct_limit,
                company_limit=args.company_limit,
                external_limit=args.external_limit,
                job_key=args.job_key,
                replay=args.replay,
                search_provider=provider,
                search_query_budget=0 if args.no_web_search else args.search_query_budget,
                search_results_per_query=args.search_results_per_query,
                candidate_page_budget=args.candidate_page_budget,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
