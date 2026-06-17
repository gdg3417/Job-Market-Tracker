from __future__ import annotations

import pytest

from src.schema import RUNS_HEADERS
from src.sheets import SheetClient, build_sprint2_run_record, normalize_header_name


class FakeWorksheet:
    def __init__(self, headers, records=None):
        self.headers = headers
        self.records = records or []
        self.appended_rows = []

    def row_values(self, row_number):
        assert row_number == 1
        return self.headers

    def get_all_records(self, numericise_ignore=None):
        assert numericise_ignore == ["all"]
        return self.records

    def append_row(self, row, value_input_option):
        assert value_input_option == "USER_ENTERED"
        self.appended_rows.append(row)


class FakeWorkbook:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, worksheet_name):
        return self.worksheets[worksheet_name]


def make_fake_client(workbook):
    client = object.__new__(SheetClient)
    client.workbook = workbook
    client._worksheet_cache = {}
    client._header_cache = {}
    return client


def test_normalize_header_name_handles_spaces_and_case():
    assert normalize_header_name("Run ID") == "run_id"
    assert normalize_header_name("Config Companies Rows") == "config_companies_rows"


def test_append_record_maps_record_to_existing_header_order():
    worksheet = FakeWorksheet(headers=["Run ID", "Status", "Extra Sheet Column"])
    client = make_fake_client(FakeWorkbook({"Scratch": worksheet}))

    client.append_record("Scratch", {"run_id": "abc", "status": "success"})

    assert worksheet.appended_rows == [["abc", "success", ""]]


def test_append_record_rejects_unmatched_record_keys():
    worksheet = FakeWorksheet(headers=["Run ID", "Status"])
    client = make_fake_client(FakeWorkbook({"Scratch": worksheet}))

    with pytest.raises(ValueError, match="not present in the header row"):
        client.append_record("Scratch", {"run_id": "abc", "status": "success", "other_key": "other"})


def test_append_run_fails_fast_when_required_runs_headers_are_missing():
    worksheet = FakeWorksheet(headers=["run_id", "status"])
    client = make_fake_client(FakeWorkbook({"Runs": worksheet}))
    record = build_sprint2_run_record(config_companies_count=25, config_searches_count=8)

    with pytest.raises(ValueError, match="missing required headers"):
        client.append_run(record)


def test_append_run_accepts_canonical_runs_headers():
    worksheet = FakeWorksheet(headers=RUNS_HEADERS)
    client = make_fake_client(FakeWorkbook({"Runs": worksheet}))
    record = build_sprint2_run_record(config_companies_count=25, config_searches_count=8)

    client.append_run(record)

    assert worksheet.appended_rows[0][0] == record["run_id"]
    assert worksheet.appended_rows[0][1] == "sprint_2_sheets_smoke_test"
    assert worksheet.appended_rows[0][13] == 25


def test_read_records_keeps_sheet_values_as_strings():
    worksheet = FakeWorksheet(headers=["company_id"], records=[{"company_id": "001"}])
    client = make_fake_client(FakeWorkbook({"Config_Companies": worksheet}))

    records = client.read_records("Config_Companies")

    assert records == [{"company_id": "001"}]


def test_build_sprint2_run_record_contains_counts_and_status():
    record = build_sprint2_run_record(config_companies_count=25, config_searches_count=8)

    assert record["status"] == "success"
    assert record["records_found"] == 33
    assert record["config_companies_rows"] == 25
    assert record["config_searches_rows"] == 8
    assert record["source_type"] == "google_sheets"
