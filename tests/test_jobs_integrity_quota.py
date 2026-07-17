from __future__ import annotations

import json
from typing import Any

import pytest

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

    def get_worksheet(self, worksheet_name: str) -> _Worksheet:
        assert worksheet_name == "Jobs"
        return self.worksheet


def test_jobs_integrity_audit_uses_shared_quota_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    operations: list[str] = []

    def fake_backoff(operation, *, operation_name: str):
        operations.append(operation_name)
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    audit = audit_jobs_integrity(_SheetClient())

    assert audit.healthy is True
    assert operations == ["audit Jobs integrity"]


def test_jobs_integrity_retry_notices_do_not_corrupt_json_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_backoff(operation, *, operation_name: str):
        assert operation_name == "audit Jobs integrity"
        print("Sheets API quota hit; retrying audit.")
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    audit = audit_jobs_integrity(_SheetClient())
    print(json.dumps(audit.to_dict()))

    captured = capsys.readouterr()
    assert json.loads(captured.out)["health_status"] == "healthy"
    assert "quota hit" not in captured.out
    assert "quota hit" in captured.err
