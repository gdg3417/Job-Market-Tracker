from __future__ import annotations

import argparse
import json
from typing import Any

from src.enrichment.company_run import preview_company_ats_queue, run_company_ats_enrichment
from src.enrichment.run import preview_direct_link_queue, run_direct_link_enrichment


def run_enrichment_pipeline(
    sheet_client: Any,
    *,
    direct_limit: int = 10,
    company_limit: int = 10,
    job_key: str = "",
    replay: bool = False,
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
    return {
        "direct_link": direct.to_dict(),
        "company_ats": company.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run direct-link enrichment followed by company career-site and ATS discovery")
    parser.add_argument("--run", action="store_true", help="Run both enrichment stages")
    parser.add_argument("--dry-run", action="store_true", help="Preview currently eligible work without writes")
    parser.add_argument("--direct-limit", type=int, default=10, help="Maximum direct URLs to process")
    parser.add_argument("--company-limit", type=int, default=10, help="Maximum company and ATS items to process")
    parser.add_argument("--job-key", default="", help="Restrict both stages to one existing Jobs job_key")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay one terminal direct-link queue item before company discovery; requires --run and --job-key",
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
                },
                indent=2,
            )
        )
        return

    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    print(
        json.dumps(
            run_enrichment_pipeline(
                sheet_client,
                direct_limit=args.direct_limit,
                company_limit=args.company_limit,
                job_key=args.job_key,
                replay=args.replay,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
