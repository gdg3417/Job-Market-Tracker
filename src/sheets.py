from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound

from src.jobs_boundaries import (
    JOBS_CANONICAL_COLUMN_COUNT,
    JOBS_IDENTITY_FIELDS,
    JOBS_WORKSHEET_NAME,
    JobsWriteBoundaryError,
    jobs_canonical_end_column,
    serialize_job_record,
    validate_canonical_write_range,
    validate_jobs_headers,
)
from src.models import JobPosting
from src.schema import CANONICAL_SCHEMA, SchemaValidationError, validate_record_headers_for_write
from src.settings import Settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
T = TypeVar("T")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_header_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def is_quota_error(error: APIError) -> bool:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return True
    message = str(error).lower()
    return "quota exceeded" in message or "[429]" in message


def with_quota_backoff(operation: Callable[[], T], *, operation_name: str) -> T:
    delays = [65, 90, 120]
    for attempt, delay_seconds in enumerate([0, *delays], start=1):
        if delay_seconds:
            print(
                f"Sheets API quota hit during {operation_name}; waiting {delay_seconds} seconds before retry {attempt}.",
                flush=True,
            )
            time.sleep(delay_seconds)
        try:
            return operation()
        except APIError as exc:
            if not is_quota_error(exc) or attempt > len(delays):
                raise
    raise RuntimeError(f"Sheets API operation failed after quota backoff: {operation_name}")


def build_sprint2_run_record(
    *,
    config_companies_count: int,
    config_searches_count: int,
    status: str = "success",
    error_message: str = "",
) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint2_sheets_smoke_{run_timestamp}",
        "run_type": "sprint_2_sheets_smoke_test",
        "source_type": "google_sheets",
        "source_name": "Job Market Tracker",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": config_companies_count + config_searches_count,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0 if status == "success" else 1,
        "rows_read": config_companies_count + config_searches_count,
        "config_companies_rows": config_companies_count,
        "config_searches_rows": config_searches_count,
        "companies_read": config_companies_count,
        "searches_read": config_searches_count,
        "error_message": error_message,
        "notes": "Sprint 2 smoke test read Config_Companies and Config_Searches, then appended this Runs row.",
        "created_at": now,
        "updated_at": now,
    }


class SheetClient:
    def __init__(self, sheet_id: str, credentials_path: str | Path):
        if not sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is required")
        if not credentials_path:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is required")

        credentials_file = Path(credentials_path).expanduser()
        if not credentials_file.exists():
            raise FileNotFoundError(f"Google credentials file was not found: {credentials_file}")

        credentials = Credentials.from_service_account_file(str(credentials_file), scopes=SCOPES)
        self.client = gspread.authorize(credentials)
        self.workbook = with_quota_backoff(lambda: self.client.open_by_key(sheet_id), operation_name="open workbook")
        self._worksheet_cache: dict[str, gspread.Worksheet] = {}
        self._header_cache: dict[str, list[str]] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "SheetClient":
        return cls(settings.google_sheet_id, settings.google_application_credentials)

    def get_worksheet(self, worksheet_name: str) -> gspread.Worksheet:
        if worksheet_name not in self._worksheet_cache:
            self._worksheet_cache[worksheet_name] = with_quota_backoff(
                lambda: self.workbook.worksheet(worksheet_name),
                operation_name=f"load worksheet {worksheet_name}",
            )
        return self._worksheet_cache[worksheet_name]

    def ensure_worksheet(self, worksheet_name: str, *, rows: int = 1000, cols: int = 26) -> gspread.Worksheet:
        if worksheet_name in self._worksheet_cache:
            return self._worksheet_cache[worksheet_name]
        try:
            worksheet = with_quota_backoff(
                lambda: self.workbook.worksheet(worksheet_name),
                operation_name=f"load worksheet {worksheet_name}",
            )
        except WorksheetNotFound:
            worksheet = with_quota_backoff(
                lambda: self.workbook.add_worksheet(title=worksheet_name, rows=rows, cols=cols),
                operation_name=f"create worksheet {worksheet_name}",
            )
        self._worksheet_cache[worksheet_name] = worksheet
        return worksheet

    @staticmethod
    def _worksheet_get_values(worksheet: Any, range_name: str | None = None) -> list[list[Any]]:
        def read_values() -> list[list[Any]]:
            if range_name:
                try:
                    return list(worksheet.get_values(range_name=range_name))
                except TypeError:
                    try:
                        return list(worksheet.get_values(range_name))
                    except TypeError:
                        return list(worksheet.get_values())
            return list(worksheet.get_values())

        return with_quota_backoff(read_values, operation_name=f"read values {getattr(worksheet, 'title', '<worksheet>')}")

    def worksheet_headers(self, worksheet_name: str) -> list[str]:
        if worksheet_name not in self._header_cache:
            worksheet = self.get_worksheet(worksheet_name)
            headers = with_quota_backoff(
                lambda: worksheet.row_values(1),
                operation_name=f"read headers {worksheet_name}",
            )
            cleaned = [header.strip() for header in headers]
            if worksheet_name == JOBS_WORKSHEET_NAME:
                cleaned = validate_jobs_headers(cleaned)
                grid_width = int(getattr(worksheet, "col_count", JOBS_CANONICAL_COLUMN_COUNT) or 0)
                if grid_width != JOBS_CANONICAL_COLUMN_COUNT:
                    raise SchemaValidationError(
                        f"Worksheet Jobs grid width {grid_width} does not equal canonical width {JOBS_CANONICAL_COLUMN_COUNT}"
                    )
            self._header_cache[worksheet_name] = cleaned
        return self._header_cache[worksheet_name]

    def _read_canonical_records(self, worksheet_name: str, worksheet: gspread.Worksheet) -> list[dict[str, Any]]:
        spec = CANONICAL_SCHEMA[worksheet_name]
        if worksheet_name == JOBS_WORKSHEET_NAME:
            self.worksheet_headers(JOBS_WORKSHEET_NAME)
        row_count = max(int(getattr(worksheet, "row_count", spec.header_row + 1) or spec.header_row + 1), spec.header_row)
        end_column = gspread.utils.rowcol_to_a1(spec.header_row, len(spec.headers)).rstrip("0123456789")
        values = self._worksheet_get_values(
            worksheet,
            f"A{spec.header_row}:{end_column}{row_count}",
        )
        if worksheet_name != JOBS_WORKSHEET_NAME:
            self._header_cache[worksheet_name] = list(spec.headers)
        header_index = max(0, int(spec.header_row) - 1)
        if len(values) <= header_index:
            return []

        actual_headers = [str(header or "").strip() for header in values[header_index][: len(spec.headers)]]
        normalized_to_index: dict[str, int] = {}
        for index, header in enumerate(actual_headers):
            normalized = normalize_header_name(header)
            if normalized and normalized not in normalized_to_index:
                normalized_to_index[normalized] = index

        missing = [header for header in spec.headers if normalize_header_name(header) not in normalized_to_index]
        if missing:
            raise SchemaValidationError(f"Worksheet {worksheet_name} is missing required headers before read: {', '.join(missing)}")

        if worksheet_name == JOBS_WORKSHEET_NAME:
            validate_jobs_headers(actual_headers)

        records: list[dict[str, Any]] = []
        for row in values[header_index + 1 :]:
            record: dict[str, Any] = {}
            for header in spec.headers:
                index = normalized_to_index[normalize_header_name(header)]
                record[header] = row[index] if index < len(row) else ""
            records.append(record)
        return records

    def read_records(self, worksheet_name: str) -> list[dict[str, Any]]:
        worksheet = self.get_worksheet(worksheet_name)
        if worksheet_name in CANONICAL_SCHEMA:
            return self._read_canonical_records(worksheet_name, worksheet)

        records = with_quota_backoff(
            lambda: worksheet.get_all_records(numericise_ignore=["all"]),
            operation_name=f"read records {worksheet_name}",
        )
        if worksheet_name not in self._header_cache:
            if records:
                self._header_cache[worksheet_name] = [str(header).strip() for header in records[0].keys()]
            else:
                self._header_cache[worksheet_name] = [
                    header.strip()
                    for header in with_quota_backoff(
                        lambda: worksheet.row_values(1),
                        operation_name=f"read headers {worksheet_name}",
                    )
                ]
        return records

    def read_records_with_row_numbers(self, worksheet_name: str) -> list[tuple[int, dict[str, Any]]]:
        records = self.read_records(worksheet_name)
        return [(index + 2, record) for index, record in enumerate(records)]

    def _record_to_row(self, worksheet_name: str, record: dict[str, Any]) -> list[Any]:
        headers = self.worksheet_headers(worksheet_name)
        if not headers:
            raise ValueError(f"Worksheet {worksheet_name} has no header row")

        if worksheet_name == JOBS_WORKSHEET_NAME:
            return serialize_job_record(record)

        validate_record_headers_for_write(worksheet_name, headers, record)
        normalized_record = {normalize_header_name(key): value for key, value in record.items()}
        matched_headers = [header for header in headers if normalize_header_name(header) in normalized_record]
        if not matched_headers:
            raise ValueError(f"No headers in worksheet {worksheet_name} matched the record keys")
        return [normalized_record.get(normalize_header_name(header), "") for header in headers]

    def _next_jobs_row_number(self, worksheet: Any) -> int:
        row_count = max(2, int(getattr(worksheet, "row_count", 2) or 2))
        values = self._worksheet_get_values(
            worksheet,
            f"A2:{jobs_canonical_end_column()}{row_count}",
        )
        identity_indexes = [list(CANONICAL_SCHEMA[JOBS_WORKSHEET_NAME].headers).index(field) for field in JOBS_IDENTITY_FIELDS]
        last_real_row = 1
        for offset, row in enumerate(values, start=2):
            if any(index < len(row) and str(row[index] or "").strip() for index in identity_indexes):
                last_real_row = offset
        return last_real_row + 1

    def _ensure_jobs_row_capacity(self, worksheet: Any, final_row: int) -> None:
        current_rows = max(1, int(getattr(worksheet, "row_count", 1) or 1))
        current_columns = max(1, int(getattr(worksheet, "col_count", 1) or 1))
        if current_columns != JOBS_CANONICAL_COLUMN_COUNT:
            raise SchemaValidationError(
                f"Worksheet Jobs grid width {current_columns} does not equal canonical width {JOBS_CANONICAL_COLUMN_COUNT}"
            )
        if final_row <= current_rows:
            return
        with_quota_backoff(
            lambda: worksheet.resize(rows=final_row, cols=JOBS_CANONICAL_COLUMN_COUNT),
            operation_name="expand Jobs row capacity",
        )

    def _write_jobs_rows(self, worksheet: Any, start_row: int, rows: list[list[Any]], *, operation_name: str) -> list[int]:
        if not rows:
            return []
        self.worksheet_headers(JOBS_WORKSHEET_NAME)
        for row in rows:
            if len(row) != JOBS_CANONICAL_COLUMN_COUNT:
                raise JobsWriteBoundaryError(
                    f"Jobs row width {len(row)} does not equal canonical width {JOBS_CANONICAL_COLUMN_COUNT}"
                )
        final_row = start_row + len(rows) - 1
        validate_canonical_write_range(
            JOBS_WORKSHEET_NAME,
            start_row,
            1,
            len(rows),
            JOBS_CANONICAL_COLUMN_COUNT,
            operation_name=operation_name,
            proposed_range=f"A{start_row}:{jobs_canonical_end_column()}{final_row}",
        )
        self._ensure_jobs_row_capacity(worksheet, final_row)
        range_name = f"A{start_row}:{jobs_canonical_end_column()}{final_row}"
        with_quota_backoff(
            lambda: worksheet.update(range_name=range_name, values=rows, value_input_option="USER_ENTERED"),
            operation_name=operation_name,
        )
        return list(range(start_row, final_row + 1))

    def append_record(self, worksheet_name: str, record: dict[str, Any]) -> int | None:
        worksheet = self.get_worksheet(worksheet_name)
        row = self._record_to_row(worksheet_name, record)
        if worksheet_name == JOBS_WORKSHEET_NAME:
            start_row = self._next_jobs_row_number(worksheet)
            return self._write_jobs_rows(
                worksheet,
                start_row,
                [row],
                operation_name="append bounded Jobs row",
            )[0]
        with_quota_backoff(
            lambda: worksheet.append_row(row, value_input_option="USER_ENTERED"),
            operation_name=f"append row {worksheet_name}",
        )
        return None

    def append_records(self, worksheet_name: str, records: list[dict[str, Any]]) -> list[int] | None:
        if not records:
            return [] if worksheet_name == JOBS_WORKSHEET_NAME else None
        worksheet = self.get_worksheet(worksheet_name)
        rows = [self._record_to_row(worksheet_name, record) for record in records]
        if worksheet_name == JOBS_WORKSHEET_NAME:
            start_row = self._next_jobs_row_number(worksheet)
            return self._write_jobs_rows(
                worksheet,
                start_row,
                rows,
                operation_name="append bounded Jobs rows",
            )
        with_quota_backoff(
            lambda: worksheet.append_rows(rows, value_input_option="USER_ENTERED"),
            operation_name=f"append rows {worksheet_name}",
        )
        return None

    def update_record(self, worksheet_name: str, row_number: int, record: dict[str, Any]) -> None:
        if row_number < 2:
            raise ValueError("Data row updates must target row 2 or later")

        worksheet = self.get_worksheet(worksheet_name)
        headers = self.worksheet_headers(worksheet_name)
        row = self._record_to_row(worksheet_name, record)
        if worksheet_name == JOBS_WORKSHEET_NAME:
            self._write_jobs_rows(
                worksheet,
                row_number,
                [row],
                operation_name=f"update bounded Jobs row {row_number}",
            )
            return
        end_cell = gspread.utils.rowcol_to_a1(row_number, len(headers))
        range_name = f"A{row_number}:{end_cell}"
        with_quota_backoff(
            lambda: worksheet.update(range_name=range_name, values=[row], value_input_option="USER_ENTERED"),
            operation_name=f"update row {worksheet_name}!{row_number}",
        )

    def append_run(self, record: dict[str, Any]) -> None:
        self.append_record("Runs", record)

    def read_jobs_with_row_numbers(self) -> list[tuple[int, JobPosting]]:
        rows = self.read_records_with_row_numbers(JOBS_WORKSHEET_NAME)
        jobs: list[tuple[int, JobPosting]] = []
        for row_number, record in rows:
            if any(str(record.get(key, "")).strip() for key in ["job_key", "company", "title", "canonical_url"]):
                jobs.append((row_number, JobPosting.from_dict(record)))
        return jobs

    def append_job(self, job: JobPosting) -> int:
        row_number = self.append_record(JOBS_WORKSHEET_NAME, job.to_dict())
        if row_number is None:
            raise RuntimeError("Bounded Jobs append did not return a row number")
        return row_number

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.update_record(JOBS_WORKSHEET_NAME, row_number, job.to_dict())

    def read_job_sources_with_row_numbers(self) -> list[tuple[int, dict[str, Any]]]:
        return self.read_records_with_row_numbers("Job_Sources")

    def append_job_source(self, record: dict[str, Any]) -> None:
        self.append_record("Job_Sources", record)

    def update_job_source(self, row_number: int, record: dict[str, Any]) -> None:
        self.update_record("Job_Sources", row_number, record)


def run_sprint2_smoke_test(settings: Settings) -> dict[str, Any]:
    sheet_client = SheetClient.from_settings(settings)
    companies = sheet_client.read_records("Config_Companies")
    searches = sheet_client.read_records("Config_Searches")

    run_record = build_sprint2_run_record(
        config_companies_count=len(companies),
        config_searches_count=len(searches),
    )
    sheet_client.append_run(record=run_record)

    return {
        "run_mode": "sprint_2_sheets_smoke_test",
        "status": "success",
        "config_companies_rows": len(companies),
        "config_searches_rows": len(searches),
        "runs_row_appended": True,
        "run_id": run_record["run_id"],
    }
