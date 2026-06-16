from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from src.settings import Settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_header_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


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
        self.workbook = self.client.open_by_key(sheet_id)

    @classmethod
    def from_settings(cls, settings: Settings) -> "SheetClient":
        return cls(settings.google_sheet_id, settings.google_application_credentials)

    def get_worksheet(self, worksheet_name: str) -> gspread.Worksheet:
        return self.workbook.worksheet(worksheet_name)

    def worksheet_headers(self, worksheet_name: str) -> list[str]:
        worksheet = self.get_worksheet(worksheet_name)
        headers = worksheet.row_values(1)
        return [header.strip() for header in headers]

    def read_records(self, worksheet_name: str) -> list[dict[str, Any]]:
        worksheet = self.get_worksheet(worksheet_name)
        return worksheet.get_all_records(numericise_ignore=["all"])

    def append_record(self, worksheet_name: str, record: dict[str, Any]) -> None:
        worksheet = self.get_worksheet(worksheet_name)
        headers = [header.strip() for header in worksheet.row_values(1)]
        if not headers:
            raise ValueError(f"Worksheet {worksheet_name} has no header row")

        normalized_record = {normalize_header_name(key): value for key, value in record.items()}
        matched_headers = [header for header in headers if normalize_header_name(header) in normalized_record]
        if not matched_headers:
            raise ValueError(f"No headers in worksheet {worksheet_name} matched the record keys")

        row = [normalized_record.get(normalize_header_name(header), "") for header in headers]
        worksheet.append_row(row, value_input_option="USER_ENTERED")

    def append_run(self, record: dict[str, Any]) -> None:
        self.append_record("Runs", record)


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
