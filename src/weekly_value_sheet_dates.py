from __future__ import annotations

import argparse
import json
from typing import Any

from src.models import JobPosting
from src.settings import load_settings
from src.sheet_dates import (
    GOOGLE_SHEETS_EPOCH,
    JOB_DATE_FIELDS,
    REJECTED_DATE_FIELDS,
    has_job_identity,
    normalize_record_dates,
    normalize_sheet_date,
)
from src.sheets import SheetClient
from src.weekly_value import apply_weekly_value

__all__ = [
    "GOOGLE_SHEETS_EPOCH",
    "JOB_DATE_FIELDS",
    "REJECTED_DATE_FIELDS",
    "normalize_record_dates",
    "normalize_sheet_date",
    "run_weekly_value_refresh",
]


def _read_optional_records(
    sheet_client: SheetClient,
    worksheet_name: str,
) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(worksheet_name)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def run_weekly_value_refresh(
    *,
    as_of: str | None = None,
    backfill_weeks: int = 12,
) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)

    job_records = sheet_client.read_records("Jobs")
    jobs = [
        JobPosting.from_dict(normalize_record_dates(record, JOB_DATE_FIELDS))
        for record in job_records
        if has_job_identity(record)
    ]
    rejected_rows = [
        normalize_record_dates(record, REJECTED_DATE_FIELDS)
        for record in _read_optional_records(sheet_client, "Rejected_Jobs")
    ]

    result = apply_weekly_value(
        sheet_client,
        as_of=as_of,
        backfill_weeks=backfill_weeks,
        jobs=jobs,
        rejected_job_rows=rejected_rows,
    )
    return {
        "run_mode": "sprint_49_weekly_value_refresh",
        "status": "success",
        "sheet_date_normalization": "shared",
        **result.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh Weekly_Value with shared Google Sheets date normalization"
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--backfill-weeks", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            run_weekly_value_refresh(
                as_of=args.as_of,
                backfill_weeks=args.backfill_weeks,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
