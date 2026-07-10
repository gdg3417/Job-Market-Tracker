from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import src.weekly_context as base
from src.models import JobPosting, parse_iso_date, today_iso, utc_now_iso
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff
from src.weekly_value import WEEKLY_VALUE_SHEET
from src.weekly_value_sheet_dates import JOB_DATE_FIELDS, normalize_record_dates

DISMISSED_REVIEW_STATUSES = {"dismissed"}


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _is_dismissed(job: JobPosting) -> bool:
    return _normalize(job.review_status) in DISMISSED_REVIEW_STATUSES


def build_weekly_context_rows(
    jobs_with_rows: list[tuple[int, JobPosting]],
    weekly_records: list[dict[str, Any]],
    *,
    as_of: str | date | None = None,
    config: base.WeeklyDigestConfig | None = None,
) -> list[dict[str, Any]]:
    actionable_jobs = [(row_number, job) for row_number, job in jobs_with_rows if not _is_dismissed(job)]
    rows = base.build_weekly_context_rows(
        actionable_jobs,
        weekly_records,
        as_of=as_of,
        config=config,
    )

    as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso()) or date.today()
    period = next((row for row in rows if row.get("item_type") == "period"), None)
    if period is not None:
        week_start = str(period.get("week_start") or "")
        week_end = str(period.get("week_end") or "")
        period["value"] = (
            f"{week_start} through {week_end} (weekly metrics); "
            f"current action items as of {as_of_date.isoformat()}"
        )
    return rows


def apply_weekly_context(
    sheet_client: SheetClient,
    *,
    as_of: str | date | None = None,
    jobs_with_rows: list[tuple[int, JobPosting]] | None = None,
    weekly_records: list[dict[str, Any]] | None = None,
    config: base.WeeklyDigestConfig | None = None,
) -> base.WeeklyContextResult:
    jobs_with_rows = jobs_with_rows if jobs_with_rows is not None else sheet_client.read_jobs_with_row_numbers()
    weekly_records = weekly_records if weekly_records is not None else sheet_client.read_records(WEEKLY_VALUE_SHEET)
    rows = build_weekly_context_rows(jobs_with_rows, weekly_records, as_of=as_of, config=config)
    values = base.build_weekly_context_values(rows)
    worksheet = base.write_weekly_context(sheet_client, values)
    warnings: list[str] = []
    try:
        requests = base._formatting_requests(base._worksheet_id(sheet_client, worksheet), len(values))
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update({"requests": requests}),
            operation_name=f"format worksheet {base.WEEKLY_CONTEXT_SHEET}",
        )
    except Exception as exc:
        warnings.append(f"Weekly_Context formatting was not applied: {exc}")

    period = next((row for row in rows if row["item_type"] == "period"), {})
    return base.WeeklyContextResult(
        jobs_read=len(jobs_with_rows),
        summary_week_start=str(period.get("week_start") or ""),
        summary_week_end=str(period.get("week_end") or ""),
        metrics_included=sum(1 for row in rows if row["item_type"] == "metric"),
        review_items=sum(1 for row in rows if row["item_type"] == "review"),
        follow_up_items=sum(1 for row in rows if row["item_type"] == "follow_up"),
        new_match_items=sum(1 for row in rows if row["item_type"] == "match"),
        rows_written=len(values),
        generated_at=utc_now_iso(),
        warnings=warnings,
    )


def run_weekly_context_refresh(
    *,
    as_of: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    raw_jobs = sheet_client.read_records("Jobs")
    jobs_with_rows = [
        (index + 2, JobPosting.from_dict(normalize_record_dates(record, JOB_DATE_FIELDS)))
        for index, record in enumerate(raw_jobs)
        if base._has_job_identity(record)
    ]
    weekly_records = sheet_client.read_records(WEEKLY_VALUE_SHEET)
    result = apply_weekly_context(
        sheet_client,
        as_of=as_of,
        jobs_with_rows=jobs_with_rows,
        weekly_records=weekly_records,
        config=base.load_weekly_digest_config(config_path),
    )
    return {"run_mode": "sprint_45_weekly_context_hotfix_refresh", "status": "success", **result.to_dict()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Weekly_Context with dismissed-role filtering")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_weekly_context_refresh(as_of=args.as_of, config_path=args.config), indent=2))


if __name__ == "__main__":
    main()
