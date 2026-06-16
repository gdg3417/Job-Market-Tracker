from __future__ import annotations

from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetClient:
    def __init__(self, sheet_id: str, credentials_path: str | Path):
        if not sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is required")
        if not credentials_path:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is required")
        credentials = Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
        self.client = gspread.authorize(credentials)
        self.workbook = self.client.open_by_key(sheet_id)

    def read_records(self, worksheet_name: str) -> list[dict[str, Any]]:
        worksheet = self.workbook.worksheet(worksheet_name)
        return worksheet.get_all_records()

    def append_record(self, worksheet_name: str, record: dict[str, Any]) -> None:
        worksheet = self.workbook.worksheet(worksheet_name)
        headers = worksheet.row_values(1)
        row = [record.get(header, "") for header in headers]
        worksheet.append_row(row, value_input_option="USER_ENTERED")
