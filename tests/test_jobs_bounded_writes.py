from __future__ import annotations

from typing import Any

import pytest

from src.jobs_integrity import JOBS_CANONICAL_COLUMN_COUNT, JobsWriteBoundaryError
from src.models import JOB_FIELDS
from src.schema import SchemaValidationError
from src.sheets import SheetClient


class FakeWorksheet:
    title = "Jobs"

    def __init__(
        self,
        *,
        rows: list[list[Any]] | None = None,
        row_count: int = 100,
        col_count: int = JOBS_CANONICAL_COLUMN_COUNT,
    ) -> None:
        self.headers = list(JOB_FIELDS)
        self.rows = [list(self.headers), *(rows or [])]
        self.row_count = row_count
        self.col_count = col_count
        self.update_calls: list[dict[str, Any]] = []
        self.resize_calls: list[tuple[int, int]] = []

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        return list(self.headers)

    def get_values(self, range_name: str | None = None) -> list[list[Any]]:
        if range_name and range_name.startswith("A2:"):
            return [list(row) for row in self.rows[1:]]
        return [list(row) for row in self.rows]

    def update(self, *, range_name: str, values: list[list[Any]], value_input_option: str) -> None:
        assert value_input_option == "USER_ENTERED"
        self.update_calls.append(
            {"range_name": range_name, "values": [list(row) for row in values], "value_input_option": value_input_option}
        )
        start_row = int(range_name.split(":", 1)[0][1:])
        for offset, row in enumerate(values):
            target_index = start_row + offset - 1
            while len(self.rows) <= target_index:
                self.rows.append([])
            self.rows[target_index] = list(row)

    def resize(self, *, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))
        self.row_count = rows
        self.col_count = cols


class FakeWorkbook:
    def __init__(self, worksheet: FakeWorksheet) -> None:
        self.worksheet_value = worksheet

    def worksheet(self, worksheet_name: str) -> FakeWorksheet:
        assert worksheet_name == "Jobs"
        return self.worksheet_value


def make_client(worksheet: FakeWorksheet) -> SheetClient:
    client = object.__new__(SheetClient)
    client.workbook = FakeWorkbook(worksheet)
    client._worksheet_cache = {}
    client._header_cache = {}
    return client


def jobs_row(**values: Any) -> list[Any]:
    row = [""] * len(JOB_FIELDS)
    for key, value in values.items():
        row[JOB_FIELDS.index(key)] = value
    return row


def jobs_record(**values: Any) -> dict[str, Any]:
    record = {field_name: "" for field_name in JOB_FIELDS}
    record.update(values)
    return record


def test_normal_jobs_append_writes_explicit_a_to_ee_range() -> None:
    worksheet = FakeWorksheet(rows=[jobs_row(job_key="existing", company="Acme", title="Manager")])
    client = make_client(worksheet)

    row_number = client.append_record("Jobs", jobs_record(job_key="new", company="Beta", title="Senior Manager"))

    assert row_number == 3
    assert worksheet.update_calls[0]["range_name"] == "A3:EE3"
    assert len(worksheet.update_calls[0]["values"][0]) == 135


def test_jobs_update_writes_explicit_matched_row() -> None:
    worksheet = FakeWorksheet(rows=[jobs_row(job_key="existing", company="Acme", title="Manager")])
    client = make_client(worksheet)

    client.update_record("Jobs", 2, jobs_record(job_key="existing", company="Acme", title="Senior Manager"))

    assert worksheet.update_calls[0]["range_name"] == "A2:EE2"
    assert len(worksheet.update_calls[0]["values"][0]) == 135


def test_distant_value_does_not_affect_next_canonical_row_calculation() -> None:
    distant_row = [""] * 8647
    distant_row[8646] = "insufficient_evidence"
    worksheet = FakeWorksheet(
        rows=[jobs_row(job_key="existing", company="Acme", title="Manager"), *([[]] * 677), distant_row],
        row_count=700,
        col_count=8647,
    )
    client = make_client(worksheet)

    assert client._next_jobs_row_number(worksheet) == 3


def test_internal_blank_row_does_not_move_append_before_last_real_job() -> None:
    worksheet = FakeWorksheet(
        rows=[
            jobs_row(job_key="row-two", company="Acme", title="Manager"),
            jobs_row(),
            jobs_row(job_key="row-four", company="Beta", title="Senior Manager"),
        ]
    )
    client = make_client(worksheet)

    assert client._next_jobs_row_number(worksheet) == 5


def test_multiple_jobs_append_to_sequential_rows_in_one_request() -> None:
    worksheet = FakeWorksheet(rows=[jobs_row(job_key="existing", company="Acme", title="Manager")])
    client = make_client(worksheet)

    rows = client.append_records(
        "Jobs",
        [
            jobs_record(job_key="new-one", company="Beta", title="Manager"),
            jobs_record(job_key="new-two", company="Gamma", title="Senior Manager"),
        ],
    )

    assert rows == [3, 4]
    assert worksheet.update_calls[0]["range_name"] == "A3:EE4"
    assert all(len(row) == 135 for row in worksheet.update_calls[0]["values"])


def test_jobs_append_rejects_unknown_field() -> None:
    worksheet = FakeWorksheet()
    client = make_client(worksheet)
    record = jobs_record(job_key="new")
    record["unexpected"] = "x"

    with pytest.raises(JobsWriteBoundaryError, match="unknown fields"):
        client.append_record("Jobs", record)


def test_jobs_write_rejects_expanded_grid_even_with_canonical_headers() -> None:
    worksheet = FakeWorksheet(col_count=136)
    client = make_client(worksheet)

    with pytest.raises(SchemaValidationError, match="grid width 136"):
        client.append_record("Jobs", jobs_record(job_key="new"))


def test_jobs_row_capacity_expands_rows_without_expanding_columns() -> None:
    worksheet = FakeWorksheet(row_count=2)
    client = make_client(worksheet)

    client.append_record("Jobs", jobs_record(job_key="new", company="Acme", title="Manager"))

    assert worksheet.resize_calls == []
    client.append_record("Jobs", jobs_record(job_key="new-two", company="Beta", title="Manager"))
    assert worksheet.resize_calls == [(3, 135)]
