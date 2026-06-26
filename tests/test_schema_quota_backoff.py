from __future__ import annotations

import pytest

import src.schema as schema_module
import src.sheets as sheets_module
from src.schema import HeaderSpec


class _Worksheet:
    def __init__(self) -> None:
        self.calls = 0

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        self.calls += 1
        return ["a", "b"]


class _SheetClient:
    def __init__(self, worksheet: _Worksheet) -> None:
        self.worksheet = worksheet

    def get_worksheet(self, worksheet_name: str) -> _Worksheet:
        assert worksheet_name == "Example"
        return self.worksheet


def test_schema_header_reads_use_quota_backoff(monkeypatch: pytest.MonkeyPatch):
    worksheet = _Worksheet()
    sheet_client = _SheetClient(worksheet)
    operations: list[str] = []

    def fake_backoff(operation, *, operation_name: str):
        operations.append(operation_name)
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    headers = schema_module._worksheet_or_empty(
        sheet_client,
        HeaderSpec("Example", ["a", "b"]),
    )

    assert headers == ["a", "b"]
    assert worksheet.calls == 1
    assert operations == ["read headers Example"]


def test_schema_does_not_mask_non_missing_worksheet_errors():
    class BrokenClient:
        def get_worksheet(self, _worksheet_name: str):
            raise RuntimeError("quota retry exhausted")

    with pytest.raises(RuntimeError, match="quota retry exhausted"):
        schema_module._worksheet_or_empty(
            BrokenClient(),
            HeaderSpec("Example", ["a"]),
        )
