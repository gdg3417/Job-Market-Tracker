from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from src.dedupe import SOURCE_FIELDS
from src.enrichment.models import ENRICHMENT_EVIDENCE_FIELDS, ENRICHMENT_QUEUE_FIELDS
from src.models import JOB_FIELDS

EXPECTED_TIMEZONE = "America/Chicago"
RUNS_HEADERS = "run_id run_type source_type source_name status started_at finished_at duration_seconds records_found records_inserted records_updated records_failed rows_read config_companies_rows config_searches_rows companies_read searches_read error_message notes created_at updated_at".split()
CONFIG_SEARCHES_HEADERS = "search_id bucket role_family include_keywords exclude_keywords locations remote_allowed hybrid_allowed salary_min salary_max role_level p_and_l_path_relevance active notes".split()
CONFIG_COMPANIES_HEADERS = "company_id company_name parent_company source_type source_slug source_url ats_platform location_focus industry_bucket company_size_bucket ownership_type priority_tier source_quality ingestion_mode active notes canonical_company_name company_aliases career_domain career_search_url ats_company_id ats_board_token enrichment_mode enrichment_active enrichment_notes".split()
SNAPSHOTS_HEADERS = "snapshot_id snapshot_date job_key company title status total_score alert_tier salary_min salary_max total_comp_estimate remote_status work_model commute_estimate_minutes role_family p_and_l_path_score growth_ownership_score notes".split()
DIGEST_HEADERS = "digest_section company title location remote_status work_model commute_estimate_minutes role_family role_level total_score alert_tier salary_min salary_max total_comp_estimate days_open first_seen_date last_seen_date canonical_url score_explanation potential_priority_score potential_priority evidence_completeness_score score_status verified_total_score verified_alert_tier enrichment_status".split()
DASHBOARD_HEADERS = ["Job Market Tracker Dashboard"]
SCORING_RULES_HEADERS = "rule_id category rule_name max_points positive_signals negative_signals scoring_logic active notes".split()
TARGET_COMPANIES_HEADERS = "target_company_id company_name parent_company industry_bucket company_size_bucket ownership_type priority_tier location_focus commute_bucket p_and_l_path_rationale role_families_to_watch score_boost_points active notes".split()
REJECTED_JOBS_HEADERS = "rejected_id source message_id thread_id subject sender received_date title company location url confidence rejection_reason extraction_notes raw_evidence created_at updated_at".split()
GMAIL_MESSAGES_HEADERS = "message_id thread_id subject sender received_at status attempt_count alerts_parsed jobs_accepted jobs_rejected error_message first_processed_at last_processed_at".split()
ENRICHMENT_QUEUE_HEADERS = list(ENRICHMENT_QUEUE_FIELDS)
ENRICHMENT_EVIDENCE_HEADERS = list(ENRICHMENT_EVIDENCE_FIELDS)


class SchemaValidationError(ValueError):
    """Raised when worksheet headers do not support safe writes."""


@dataclass(frozen=True, slots=True)
class HeaderSpec:
    worksheet_name: str
    headers: list[str]
    header_row: int = 1
    order_matters: bool = True


@dataclass(slots=True)
class HeaderValidationResult:
    worksheet_name: str
    header_row: int
    expected_headers: list[str]
    actual_headers: list[str]
    missing_headers: list[str]
    extra_headers: list[str]
    order_difference: bool = False

    @property
    def ok(self) -> bool:
        return not self.missing_headers and not self.extra_headers and not self.order_difference

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


@dataclass(slots=True)
class WorkbookValidationResult:
    timezone: str
    expected_timezone: str
    timezone_ok: bool
    sheets: list[HeaderValidationResult]

    @property
    def ok(self) -> bool:
        return self.timezone_ok and all(sheet.ok for sheet in self.sheets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "timezone": self.timezone,
            "expected_timezone": self.expected_timezone,
            "timezone_ok": self.timezone_ok,
            "sheets": [sheet.to_dict() for sheet in self.sheets],
        }


CANONICAL_SCHEMA = {
    "Jobs": HeaderSpec("Jobs", list(JOB_FIELDS)),
    "Job_Sources": HeaderSpec("Job_Sources", list(SOURCE_FIELDS)),
    "Runs": HeaderSpec("Runs", RUNS_HEADERS),
    "Dashboard": HeaderSpec("Dashboard", DASHBOARD_HEADERS),
    "Digest": HeaderSpec("Digest", DIGEST_HEADERS, header_row=5),
    "Snapshots": HeaderSpec("Snapshots", SNAPSHOTS_HEADERS),
    "Config_Searches": HeaderSpec("Config_Searches", CONFIG_SEARCHES_HEADERS),
    "Config_Companies": HeaderSpec("Config_Companies", CONFIG_COMPANIES_HEADERS),
    "Scoring_Rules": HeaderSpec("Scoring_Rules", SCORING_RULES_HEADERS),
    "Target_Companies": HeaderSpec("Target_Companies", TARGET_COMPANIES_HEADERS),
    "Rejected_Jobs": HeaderSpec("Rejected_Jobs", REJECTED_JOBS_HEADERS),
    "Gmail_Messages": HeaderSpec("Gmail_Messages", GMAIL_MESSAGES_HEADERS),
    "Enrichment_Queue": HeaderSpec("Enrichment_Queue", ENRICHMENT_QUEUE_HEADERS),
    "Enrichment_Evidence": HeaderSpec("Enrichment_Evidence", ENRICHMENT_EVIDENCE_HEADERS),
}


def normalize_header_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _trim(values: Iterable[Any]) -> list[str]:
    headers = [str(value).strip() for value in values]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, rem = divmod(number - 1, 26)
        value = chr(65 + rem) + value
    return value


def expected_headers_for(worksheet_name: str) -> list[str]:
    spec = CANONICAL_SCHEMA.get(worksheet_name)
    return list(spec.headers) if spec else []


def compare_headers(spec: HeaderSpec, actual_headers: Iterable[Any]) -> HeaderValidationResult:
    actual = _trim(actual_headers)
    actual_normalized = [normalize_header_name(header) for header in actual if str(header).strip()]
    expected_normalized = [normalize_header_name(header) for header in spec.headers]
    actual_set = set(actual_normalized)
    expected_set = set(expected_normalized)
    missing = [header for header in spec.headers if normalize_header_name(header) not in actual_set]
    extra = [header for header in actual if normalize_header_name(header) not in expected_set]
    order_difference = spec.order_matters and not missing and not extra and actual_normalized != expected_normalized
    return HeaderValidationResult(spec.worksheet_name, spec.header_row, list(spec.headers), actual, missing, extra, order_difference)


def validate_record_headers_for_write(worksheet_name: str, actual_headers: Iterable[Any], record: dict[str, Any]) -> None:
    actual_normalized = {normalize_header_name(header) for header in _trim(actual_headers) if str(header).strip()}
    missing = [header for header in expected_headers_for(worksheet_name) if normalize_header_name(header) not in actual_normalized]
    if missing:
        raise SchemaValidationError(f"Worksheet {worksheet_name} is missing required headers before write: {', '.join(missing)}")
    unmatched = [key for key in record if normalize_header_name(key) not in actual_normalized]
    if unmatched:
        raise SchemaValidationError(
            f"Record for worksheet {worksheet_name} has keys that are not present in the header row: {', '.join(unmatched)}"
        )


def _metadata(sheet_client: Any) -> dict[str, Any]:
    from src.sheets import with_quota_backoff

    return with_quota_backoff(lambda: sheet_client.workbook.fetch_sheet_metadata(), operation_name="fetch workbook metadata")


def _worksheet_or_empty(sheet_client: Any, spec: HeaderSpec) -> list[str]:
    try:
        worksheet = sheet_client.get_worksheet(spec.worksheet_name)
    except Exception:
        return []
    return worksheet.row_values(spec.header_row)


def validate_workbook(sheet_client: Any) -> WorkbookValidationResult:
    timezone = str((_metadata(sheet_client).get("properties") or {}).get("timeZone") or "")
    results = []
    for spec in CANONICAL_SCHEMA.values():
        results.append(compare_headers(spec, _worksheet_or_empty(sheet_client, spec)))
    return WorkbookValidationResult(timezone, EXPECTED_TIMEZONE, timezone == EXPECTED_TIMEZONE, results)


def validate_workbook_or_raise(sheet_client: Any) -> WorkbookValidationResult:
    result = validate_workbook(sheet_client)
    if result.ok:
        return result
    failed = [sheet.worksheet_name for sheet in result.sheets if not sheet.ok]
    if not result.timezone_ok:
        failed.append("workbook_timezone")
    raise SchemaValidationError(f"Workbook schema validation failed: {', '.join(failed)}")


def _ensure_schema_worksheet(sheet_client: Any, worksheet_name: str, rows: int, cols: int) -> Any:
    if hasattr(sheet_client, "ensure_worksheet"):
        return sheet_client.ensure_worksheet(worksheet_name, rows=rows, cols=cols)
    return sheet_client.get_worksheet(worksheet_name)


def _ensure_grid_capacity(worksheet: Any, *, rows: int, cols: int, worksheet_name: str) -> None:
    """Expand an existing worksheet before writing headers beyond its current grid."""
    if not hasattr(worksheet, "resize"):
        return

    current_rows = int(getattr(worksheet, "row_count", rows) or rows)
    current_cols = int(getattr(worksheet, "col_count", cols) or cols)
    target_rows = max(current_rows, rows)
    target_cols = max(current_cols, cols)
    if target_rows == current_rows and target_cols == current_cols:
        return

    from src.sheets import with_quota_backoff

    with_quota_backoff(
        lambda: worksheet.resize(rows=target_rows, cols=target_cols),
        operation_name=f"resize worksheet {worksheet_name}",
    )


def _clear_header_cache(sheet_client: Any) -> None:
    if hasattr(sheet_client, "_header_cache"):
        sheet_client._header_cache.clear()


def migrate_trailing_headers(sheet_client: Any) -> WorkbookValidationResult:
    """Append canonical trailing headers and return validation from the same read pass."""
    from src.sheets import with_quota_backoff

    results: list[HeaderValidationResult] = []
    for spec in CANONICAL_SCHEMA.values():
        required_rows = max(1000, spec.header_row + 10)
        required_cols = len(spec.headers)
        worksheet = _ensure_schema_worksheet(
            sheet_client,
            spec.worksheet_name,
            rows=required_rows,
            cols=required_cols,
        )
        _ensure_grid_capacity(
            worksheet,
            rows=required_rows,
            cols=required_cols,
            worksheet_name=spec.worksheet_name,
        )
        current = _trim(worksheet.row_values(spec.header_row))
        final_headers = list(current)
        if current != spec.headers:
            if not current:
                start_index = 1
                missing = list(spec.headers)
            else:
                current_normalized = [normalize_header_name(header) for header in current]
                expected_prefix = [normalize_header_name(header) for header in spec.headers[: len(current)]]
                if current_normalized != expected_prefix:
                    raise SchemaValidationError(
                        f"Worksheet {spec.worksheet_name} cannot be migrated safely because existing headers are not a canonical prefix"
                    )
                start_index = len(current) + 1
                missing = spec.headers[len(current) :]
            if missing:
                end_index = start_index + len(missing) - 1
                cell_range = f"{_column_name(start_index)}{spec.header_row}:{_column_name(end_index)}{spec.header_row}"
                with_quota_backoff(
                    lambda worksheet=worksheet, cell_range=cell_range, missing=missing: worksheet.update(
                        range_name=cell_range,
                        values=[missing],
                        value_input_option="USER_ENTERED",
                    ),
                    operation_name=f"migrate trailing headers {spec.worksheet_name}",
                )
                final_headers.extend(missing)
        results.append(compare_headers(spec, final_headers))

    metadata = _metadata(sheet_client)
    timezone = str((metadata.get("properties") or {}).get("timeZone") or "")
    if timezone != EXPECTED_TIMEZONE:
        with_quota_backoff(
            lambda: sheet_client.workbook.batch_update(
                {"requests": [{"updateSpreadsheetProperties": {"properties": {"timeZone": EXPECTED_TIMEZONE}, "fields": "timeZone"}}]}
            ),
            operation_name="migrate workbook timezone",
        )
        timezone = EXPECTED_TIMEZONE
    _clear_header_cache(sheet_client)
    return WorkbookValidationResult(timezone, EXPECTED_TIMEZONE, timezone == EXPECTED_TIMEZONE, results)


def repair_headers(sheet_client: Any) -> None:
    from src.sheets import with_quota_backoff

    for spec in CANONICAL_SCHEMA.values():
        required_rows = max(1000, spec.header_row + 10)
        worksheet = _ensure_schema_worksheet(
            sheet_client,
            spec.worksheet_name,
            rows=required_rows,
            cols=len(spec.headers),
        )
        current = _trim(worksheet.row_values(spec.header_row))
        width = max(len(current), len(spec.headers), 1)
        _ensure_grid_capacity(
            worksheet,
            rows=required_rows,
            cols=width,
            worksheet_name=spec.worksheet_name,
        )
        values = [*spec.headers, *[""] * (width - len(spec.headers))]
        cell_range = f"A{spec.header_row}:{_column_name(width)}{spec.header_row}"
        with_quota_backoff(
            lambda worksheet=worksheet, cell_range=cell_range, values=values: worksheet.update(
                range_name=cell_range,
                values=[values],
                value_input_option="USER_ENTERED",
            ),
            operation_name=f"repair headers {spec.worksheet_name}",
        )
    with_quota_backoff(
        lambda: sheet_client.workbook.batch_update(
            {"requests": [{"updateSpreadsheetProperties": {"properties": {"timeZone": EXPECTED_TIMEZONE}, "fields": "timeZone"}}]}
        ),
        operation_name="repair workbook timezone",
    )
    _clear_header_cache(sheet_client)


def _load_sheet_client() -> Any:
    from src.settings import load_settings
    from src.sheets import SheetClient

    return SheetClient.from_settings(load_settings())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or repair Job Market Tracker workbook schema")
    parser.add_argument("--validate", action="store_true", help="Validate workbook tab headers and timezone")
    parser.add_argument("--migrate", action="store_true", help="Append missing canonical trailing headers without moving existing data")
    parser.add_argument("--repair-headers", action="store_true", help="Overwrite canonical header rows and set Central timezone")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.validate and not args.migrate and not args.repair_headers:
        args.validate = True
    sheet_client = _load_sheet_client()
    migration_result: WorkbookValidationResult | None = None
    if args.migrate:
        migration_result = migrate_trailing_headers(sheet_client)
    if args.repair_headers:
        repair_headers(sheet_client)
    if args.validate or args.repair_headers:
        result = validate_workbook(sheet_client)
    elif migration_result is not None:
        result = migration_result
    else:
        result = validate_workbook(sheet_client)
    print(json.dumps(result.to_dict(), indent=2))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
