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

from src.models import JobPosting
from src.schema import validate_record_headers_for_write
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

    def worksheet_headers(self, worksheet_name: str) -> list[str]:
        if worksheet_name not in self._header_cache:
            worksheet = self.get_worksheet(worksheet_name)
            headers = with_quota_backoff(
                lambda: worksheet.row_values(1),
                operation_name=f"read headers {worksheet_name}",
            )
            self._header_cache[worksheet_name] = [header.strip() for header in headers]
        return self._header_cache[worksheet_name]

    def read_records(self, worksheet_name: str) -> list[dict[str, Any]]:
        worksheet = self.get_worksheet(worksheet_name)
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

        validate_record_headers_for_write(worksheet_name, headers, record)

        normalized_record = {normalize_header_name(key): value for key, value in record.items()}
        matched_headers = [header for header in headers if normalize_header_name(header) in normalized_record]
        if not matched_headers:
            raise ValueError(f"No headers in worksheet {worksheet_name} matched the record keys")

        return [normalized_record.get(normalize_header_name(header), "") for header in headers]

    def append_record(self, worksheet_name: str, record: dict[str, Any]) -> None:
        worksheet = self.get_worksheet(worksheet_name)
        row = self._record_to_row(worksheet_name, record)
        with_quota_backoff(
            lambda: worksheet.append_row(row, value_input_option="USER_ENTERED"),
            operation_name=f"append row {worksheet_name}",
        )

    def append_records(self, worksheet_name: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        worksheet = self.get_worksheet(worksheet_name)
        rows = [self._record_to_row(worksheet_name, record) for record in records]
        with_quota_backoff(
            lambda: worksheet.append_rows(rows, value_input_option="USER_ENTERED"),
            operation_name=f"append rows {worksheet_name}",
        )

    def update_record(self, worksheet_name: str, row_number: int, record: dict[str, Any]) -> None:
        if row_number < 2:
            raise ValueError("Data row updates must target row 2 or later")

        worksheet = self.get_worksheet(worksheet_name)
        headers = self.worksheet_headers(worksheet_name)
        row = self._record_to_row(worksheet_name, record)
        end_cell = gspread.utils.rowcol_to_a1(row_number, len(headers))
        range_name = f"A{row_number}:{end_cell}"
        with_quota_backoff(
            lambda: worksheet.update(range_name=range_name, values=[row], value_input_option="USER_ENTERED"),
            operation_name=f"update row {worksheet_name}!{row_number}",
        )

    def append_run(self, record: dict[str, Any]) -> None:
        self.append_record("Runs", record)

    def read_jobs_with_row_numbers(self) -> list[tuple[int, JobPosting]]:
        rows = self.read_records_with_row_numbers("Jobs")
        jobs: list[tuple[int, JobPosting]] = []
        for row_number, record in rows:
            if any(str(record.get(key, "")).strip() for key in ["job_key", "company", "title", "canonical_url"]):
                jobs.append((row_number, JobPosting.from_dict(record)))
        return jobs

    def append_job(self, job: JobPosting) -> None:
        self.append_record("Jobs", job.to_dict())

    def update_job(self, row_number: int, job: JobPosting) -> None:
        self.update_record("Jobs", row_number, job.to_dict())

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
    sheet_client.append_run(run_record)

    return {
        "run_mode": "sprint_2_sheets_smoke_test",
        "status": "success",
        "config_companies_rows": len(companies),
        "config_searches_rows": len(searches),
        "runs_row_appended": True,
        "run_id": run_record["run_id"],
    }
