from __future__ import annotations

from typing import Any

import pytest

import src.jobs_integrity as jobs_integrity_module
import src.sheets as sheets_module
from src.jobs_integrity import JOBS_CANONICAL_COLUMN_COUNT, audit_jobs_integrity
from src.models import JOB_FIELDS


class _Worksheet:
    row_count = 2
    col_count = JOBS_CANONICAL_COLUMN_COUNT
    id = 44

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        return list(JOB_FIELDS)

    def get_values(self, range_name: str | None = None) -> list[list[Any]]:
        assert range_name == f"A1:EE{self.row_count}"
        return [list(JOB_FIELDS)]


class _SheetClient:
    def __init__(self) -> None:
        self.worksheet = _Worksheet()
        self.workbook = object()
        self.load_count = 0

    def get_worksheet(self, worksheet_name: str) -> _Worksheet:
        assert worksheet_name == "Jobs"
        self.load_count += 1
        return self.worksheet


class _QuotaAwareSheetClient(_SheetClient):
    def get_worksheet(self, worksheet_name: str) -> _Worksheet:
        assert worksheet_name == "Jobs"
        self.load_count += 1
        return sheets_module.with_quota_backoff(
            lambda: self.worksheet,
            operation_name="load worksheet Jobs",
        )


def test_jobs_integrity_loads_worksheet_before_audit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []

    def fake_backoff(operation, *, operation_name: str):
        operations.append(operation_name)
        if operation_name == "load worksheet Jobs":
            raise RuntimeError("worksheet quota retries exhausted")
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    client = _QuotaAwareSheetClient()
    with pytest.raises(RuntimeError, match="worksheet quota retries exhausted"):
        audit_jobs_integrity(client)

    assert client.load_count == 1
    assert operations == ["load worksheet Jobs"]


def test_jobs_integrity_reuses_preloaded_worksheet_inside_audit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []

    def fake_backoff(operation, *, operation_name: str):
        operations.append(operation_name)
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    client = _SheetClient()
    audit = audit_jobs_integrity(client)

    assert audit.healthy is True
    assert client.load_count == 1
    assert operations == ["audit Jobs integrity"]



def test_jobs_integrity_load_retry_notices_stay_off_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_backoff(operation, *, operation_name: str):
        if operation_name == "load worksheet Jobs":
            print("Sheets API quota hit while loading Jobs")
        elif operation_name != "audit Jobs integrity":
            raise AssertionError(f"Unexpected retry operation: {operation_name}")
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    audit = audit_jobs_integrity(_QuotaAwareSheetClient())

    captured = capsys.readouterr()
    assert audit.healthy is True
    assert captured.out == ""
    assert "quota hit while loading Jobs" in captured.err


def test_jobs_integrity_client_load_retry_notices_stay_off_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = _SheetClient()

    monkeypatch.setattr("src.settings.load_settings", lambda: object())

    def fake_from_settings(settings: object) -> _SheetClient:
        assert settings is not None
        print("Sheets API quota hit while opening workbook")
        return sentinel

    monkeypatch.setattr(
        sheets_module.SheetClient,
        "from_settings",
        staticmethod(fake_from_settings),
    )

    assert jobs_integrity_module._load_sheet_client() is sentinel

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "quota hit while opening workbook" in captured.err
