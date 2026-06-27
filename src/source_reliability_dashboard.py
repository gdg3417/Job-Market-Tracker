from __future__ import annotations

import argparse
import json
from typing import Any

from src.source_reliability import SourceHealthState, platform_health_metrics, read_source_health, utc_now_iso


def build_source_reliability_dashboard_rows(sheet_client: Any, *, generated_at: str | None = None) -> list[list[Any]]:
    timestamp = generated_at or utc_now_iso()
    states = [state for _, state in read_source_health(sheet_client).values()]
    metrics = platform_health_metrics(states)
    rows: list[list[Any]] = [
        ["ATS platform source reliability"],
        ["Generated at", timestamp],
        [
            "Platform",
            "Sources",
            "Requests",
            "Successes",
            "Failures",
            "Jobs returned",
            "Jobs accepted",
            "Average latency ms",
            "Rate-limit events",
            "Paused sources",
            "Invalid configurations",
            "Failure categories",
        ],
    ]
    for platform, data in sorted(metrics.items(), key=lambda item: item[0]):
        rows.append(
            [
                platform,
                data.get("sources", 0),
                data.get("requests", 0),
                data.get("successes", 0),
                data.get("failures", 0),
                data.get("jobs_returned", 0),
                data.get("jobs_accepted", 0),
                data.get("average_latency_ms", 0),
                data.get("rate_limit_events", 0),
                data.get("paused_sources", 0),
                data.get("invalid_configurations", 0),
                json.dumps(data.get("failures_by_category") or {}, sort_keys=True),
            ]
        )
    if len(rows) == 3:
        rows.append(["No source reliability rows recorded yet", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "{}"])
    return rows


def write_source_reliability_dashboard_section(sheet_client: Any, *, generated_at: str | None = None) -> int:
    if not hasattr(sheet_client, "get_worksheet"):
        return 0
    from src.sheets import with_quota_backoff

    worksheet = sheet_client.get_worksheet("Dashboard")
    existing = with_quota_backoff(
        lambda: worksheet.get_all_values(),
        operation_name="read Dashboard before source reliability write",
    )
    start_row = max(1, len(existing) + 2)
    rows = build_source_reliability_dashboard_rows(sheet_client, generated_at=generated_at)
    with_quota_backoff(
        lambda: worksheet.update(
            range_name=f"A{start_row}",
            values=rows,
            value_input_option="USER_ENTERED",
        ),
        operation_name="write Dashboard source reliability section",
    )
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render source reliability metrics for Dashboard review")
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--dry-run", action="store_true")
    execution.add_argument("--write-dashboard", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from src.schema import validate_workbook_or_raise
    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    validate_workbook_or_raise(sheet_client)
    if args.dry_run:
        print(json.dumps({"rows": build_source_reliability_dashboard_rows(sheet_client)}, indent=2))
        return
    rows_written = write_source_reliability_dashboard_section(sheet_client)
    print(json.dumps({"status": "success", "rows_written": rows_written}, indent=2))


if __name__ == "__main__":
    main()
