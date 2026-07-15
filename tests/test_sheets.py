from __future__ import annotations

import pytest

from src.jobs_integrity import JobsIntegrityError
from src.models import JOB_FIELDS
from src.schema import RUNS_HEADERS
from src.sheets import SheetClient, build_sprint2_run_record, normalize_header_name


class FakeWorksheet:
    def __init__(self, headers, records=None, values=None):
        self.headers = headers
        self.records = records or []
        self.values = values
        self.appended_rows = []
        self.get_all_records_called = False

    def row_values(self, row_number):
        assert row_number == 1
        return self.headers

    def get_values(self):
        return self.values if self.values is not None else [self.headers]

    def get_all_records(self, numericise_ignore=None):
        self.get_all_records_called = True
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


def _jobs_row(**values):
    row = [""] * len(JOB_FIELDS)
    for key, value in values.items():
        row[JOB_FIELDS.index(key)] = value
    return row


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


def test_read_records_keeps_sheet_values_as_strings_for_noncanonical_tabs():
    worksheet = FakeWorksheet(headers=["company_id"], records=[{"company_id": "001"}])
    client = make_fake_client(FakeWorkbook({"Scratch": worksheet}))

    records = client.read_records("Scratch")

    assert records == [{"company_id": "001"}]
    assert worksheet.get_all_records_called is True


def test_canonical_read_ignores_extra_blank_headers_created_by_wide_sheet_range():
    worksheet = FakeWorksheet(
        headers=JOB_FIELDS,
        values=[
            [*JOB_FIELDS, "", ""],
            [*_jobs_row(job_key="acme-manager", company="Acme", title="Manager, Strategy", canonical_url="https://example.com/job"), "", "stray value"],
        ],
    )
    client = make_fake_client(FakeWorkbook({"Jobs": worksheet}))

    records = client.read_records("Jobs")

    assert len(records) == 1
    assert records[0]["job_key"] == "acme-manager"
    assert records[0]["company"] == "Acme"
    assert records[0]["title"] == "Manager, Strategy"
    assert records[0]["canonical_url"] == "https://example.com/job"
    assert worksheet.get_all_records_called is False


def test_canonical_read_preserves_blank_rows_so_row_numbers_remain_aligned():
    worksheet = FakeWorksheet(
        headers=JOB_FIELDS,
        values=[
            JOB_FIELDS,
            _jobs_row(),
            _jobs_row(job_key="row-three", company="Acme", title="Manager, Strategy", canonical_url="https://example.com/job"),
        ],
    )
    client = make_fake_client(FakeWorkbook({"Jobs": worksheet}))

    rows = client.read_records_with_row_numbers("Jobs")

    assert rows[0][0] == 2
    assert rows[0][1]["job_key"] == ""
    assert rows[1][0] == 3
    assert rows[1][1]["job_key"] == "row-three"


def test_canonical_read_still_fails_when_required_header_is_missing():
    headers = [header for header in JOB_FIELDS if header != "title"]
    worksheet = FakeWorksheet(headers=headers, values=[headers, ["" for _ in headers]])
    client = make_fake_client(FakeWorkbook({"Jobs": worksheet}))

    with pytest.raises(JobsIntegrityError, match="title"):
        client.read_records("Jobs")


def test_build_sprint2_run_record_contains_counts_and_status():
    record = build_sprint2_run_record(config_companies_count=25, config_searches_count=8)

    assert record["status"] == "success"
    assert record["records_found"] == 33
    assert record["config_companies_rows"] == 25
    assert record["config_searches_rows"] == 8
    assert record["source_type"] == "google_sheets"
