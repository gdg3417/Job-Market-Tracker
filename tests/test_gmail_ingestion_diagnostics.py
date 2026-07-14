import base64
from pathlib import Path
from types import SimpleNamespace

import src.gmail_ingestion as gmail_ingestion
from src.gmail_diagnostics import GmailAuthenticationError, classify_failure
from src.schema import GMAIL_MESSAGES_HEADERS
from src.sources.gmail_alerts import GmailAlertEmail

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring_rules.yml"


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.payload


class FakeGmailService:
    def __init__(self, messages=None, ordered_ids=None):
        self.message_payloads = messages or {}
        self.ordered_ids = ordered_ids or list(self.message_payloads)
        self.get_calls = []

    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        if "labelIds" not in kwargs:
            return FakeRequest({"labels": [{"id": "Label_1", "name": "Job Tracker"}]})
        refs = [{"id": message_id, "threadId": f"thread-{message_id}"} for message_id in self.ordered_ids]
        return FakeRequest({"messages": refs, "resultSizeEstimate": len(refs)})

    def get(self, **kwargs):
        message_id = kwargs["id"]
        self.get_calls.append(message_id)
        payload = self.message_payloads[message_id]
        if isinstance(payload, Exception):
            return FakeRequest(error=payload)
        return FakeRequest(payload)


class FakeWorksheet:
    def __init__(self, sheet_client, worksheet_name):
        self.sheet_client = sheet_client
        self.worksheet_name = worksheet_name

    def row_values(self, row_number):
        assert row_number == 1
        return list(self.sheet_client.headers.get(self.worksheet_name, []))

    def update(self, *, range_name, values, value_input_option):
        del range_name, value_input_option
        self.sheet_client.headers[self.worksheet_name] = list(values[0])


class FakeSheetClient:
    def __init__(self):
        self.tables = {
            "Gmail_Messages": [],
            "Gmail_Failures": [],
            "Rejected_Jobs": [],
            "Jobs": [],
            "Job_Sources": [],
            "Runs": [],
        }
        self.headers = {}
        self._header_cache = {}

    def ensure_worksheet(self, worksheet_name, *, rows, cols):
        del rows, cols
        self.tables.setdefault(worksheet_name, [])
        return FakeWorksheet(self, worksheet_name)

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(record)) for index, record in enumerate(self.tables[worksheet_name])]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)

    def append_job(self, job):
        self.tables["Jobs"].append(job.to_dict())

    def update_job(self, row_number, job):
        self.tables["Jobs"][row_number - 2] = job.to_dict()

    def append_job_source(self, record):
        self.tables["Job_Sources"].append(dict(record))

    def update_job_source(self, row_number, record):
        self.tables["Job_Sources"][row_number - 2] = dict(record)

    def append_run(self, record):
        self.tables["Runs"].append(dict(record))


def _encoded(value):
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _single_job_message(message_id):
    email = GmailAlertEmail(
        message_id=message_id,
        subject="Director, Business Operations at Acme",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Mon, 22 Jun 2026 10:44:27 +0000",
        body_text=f"New jobs match your preferences\nhttps://www.linkedin.com/jobs/view/6000000{message_id[-1:] or '1'}/",
    )
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": email.subject},
                {"name": "From", "value": email.sender},
                {"name": "Date", "value": email.received_at},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": _encoded(email.body_text)}}],
        },
    }


def _patch_runner(monkeypatch, sheet_client, service, *, max_results=50):
    settings = SimpleNamespace(
        gmail_client_config="gmail-client.json",
        gmail_token_json="gmail-token.json",
        gmail_label_name="Job Tracker",
        gmail_max_results=max_results,
        scoring_rules_path=RULES_PATH,
    )
    monkeypatch.setattr(gmail_ingestion, "load_settings", lambda: settings)
    monkeypatch.setattr(gmail_ingestion.SheetClient, "from_settings", staticmethod(lambda ignored: sheet_client))
    monkeypatch.setattr(gmail_ingestion, "build_gmail_service", lambda client, token: service)


def _ledger_row(message_id, status, attempt_count):
    row = {header: "" for header in GMAIL_MESSAGES_HEADERS}
    row.update(
        {
            "message_id": message_id,
            "thread_id": f"thread-{message_id}",
            "status": status,
            "attempt_count": attempt_count,
        }
    )
    return row


class APIError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status_code)


def test_cell_limit_error_is_classified_as_retryable_workbook_failure():
    error = APIError("This action would increase the number of cells above the limit of 10000000 cells")

    diagnostic = classify_failure(error, stage="write_jobs")

    assert diagnostic.category == "workbook_write"
    assert diagnostic.stage == "write_jobs"
    assert diagnostic.retry_eligible is True
    assert "10000000" in diagnostic.message


def test_all_shared_workbook_failures_are_systemic_and_not_quarantined(monkeypatch):
    sheet_client = FakeSheetClient()
    sheet_client.tables["Gmail_Messages"] = [
        _ledger_row("m1", "retryable_failure", 2),
        _ledger_row("m2", "retryable_failure", 2),
    ]
    service = FakeGmailService({"m1": _single_job_message("m1"), "m2": _single_job_message("m2")})
    _patch_runner(monkeypatch, sheet_client, service)
    error = APIError("This action would increase the number of cells above the limit of 10000000 cells")
    monkeypatch.setattr(gmail_ingestion, "upsert_jobs", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    result = gmail_ingestion.run_gmail_ingestion(max_message_attempts=3)

    assert result["status"] == "all_new_messages_failed"
    assert result["systemic_failure_category"] == "workbook_write"
    assert result["systemic_failure_stage"] == "write_jobs"
    assert result["failed_messages"] == 2
    assert result["quarantined_messages"] == 0
    assert result["ingestion_run_recorded"] is True
    assert len(sheet_client.tables["Runs"]) == 1
    assert {row["status"] for row in sheet_client.tables["Gmail_Messages"]} == {"retryable_failure"}
    assert len(sheet_client.tables["Gmail_Failures"]) == 2
    assert {row["systemic_failure"] for row in sheet_client.tables["Gmail_Failures"]} == {"true"}
    assert {row["error_category"] for row in sheet_client.tables["Gmail_Failures"]} == {"workbook_write"}


def test_isolated_parser_failure_is_quarantined_with_message_diagnostics(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService({"m1": _single_job_message("m1")})
    _patch_runner(monkeypatch, sheet_client, service)
    monkeypatch.setattr(
        gmail_ingestion,
        "parse_job_alert_email",
        lambda email: (_ for _ in ()).throw(ValueError("malformed alert structure")),
    )

    result = gmail_ingestion.run_gmail_ingestion()

    assert result["status"] == "completed_with_quarantine"
    assert result["processing_failures"] == 1
    assert result["failed_messages"] == 0
    assert result["quarantined_messages"] == 1
    assert result["backlog_remaining"] == 0
    assert sheet_client.tables["Gmail_Messages"][0]["status"] == "quarantined"
    diagnostic = sheet_client.tables["Gmail_Failures"][0]
    assert diagnostic["message_id"] == "m1"
    assert diagnostic["failure_stage"] == "parse_message"
    assert diagnostic["error_category"] == "parsing"
    assert diagnostic["retry_eligible"] == "false"
    assert diagnostic["subject"]
    assert diagnostic["sender"]
    assert diagnostic["received_at"]
    assert diagnostic["last_attempt_at"]


def test_retryable_isolated_failure_quarantines_after_bounded_attempts(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService({"m1": RuntimeError("temporary Gmail error")})
    _patch_runner(monkeypatch, sheet_client, service)

    first = gmail_ingestion.run_gmail_ingestion(max_message_attempts=3)
    second = gmail_ingestion.run_gmail_ingestion(max_message_attempts=3)
    third = gmail_ingestion.run_gmail_ingestion(max_message_attempts=3)

    assert first["failed_messages"] == 1
    assert second["failed_messages"] == 1
    assert third["status"] == "completed_with_quarantine"
    assert third["failed_messages"] == 0
    assert third["quarantined_messages"] == 1
    assert sheet_client.tables["Gmail_Messages"][0]["attempt_count"] == 3
    assert sheet_client.tables["Gmail_Messages"][0]["status"] == "quarantined"
    assert len(sheet_client.tables["Gmail_Failures"]) == 3
    assert sheet_client.tables["Gmail_Failures"][-1]["retry_eligible"] == "false"


def test_failed_only_replay_does_not_process_completed_or_unseen_messages(monkeypatch):
    sheet_client = FakeSheetClient()
    sheet_client.tables["Gmail_Messages"] = [
        _ledger_row("m1", "success", 1),
        _ledger_row("m2", "retryable_failure", 1),
    ]
    service = FakeGmailService(
        {"m1": _single_job_message("m1"), "m2": _single_job_message("m2"), "m3": _single_job_message("m3")},
        ordered_ids=["m1", "m2", "m3"],
    )
    _patch_runner(monkeypatch, sheet_client, service)

    result = gmail_ingestion.run_gmail_ingestion(retry_failed_only=True)

    assert result["replay_mode"] == "failed_only"
    assert result["messages_already_processed"] == 1
    assert result["messages_not_selected"] == 1
    assert result["new_messages_processed"] == 1
    assert result["backlog_remaining"] == 1
    assert service.get_calls == ["m2"]


def test_selected_force_replay_keeps_jobs_and_sources_idempotent(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService({"m1": _single_job_message("m1")})
    _patch_runner(monkeypatch, sheet_client, service)

    first = gmail_ingestion.run_gmail_ingestion()
    second = gmail_ingestion.run_gmail_ingestion(
        force_reprocess=True,
        message_ids=["m1"],
    )

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert second["replay_mode"] == "selected"
    assert len(sheet_client.tables["Jobs"]) == 1
    assert len(sheet_client.tables["Job_Sources"]) == 1
    assert sheet_client.tables["Gmail_Messages"][0]["attempt_count"] == 2


def test_force_reprocess_without_exact_ids_is_rejected(monkeypatch):
    settings = SimpleNamespace(
        gmail_client_config="gmail-client.json",
        gmail_token_json="gmail-token.json",
        gmail_label_name="Job Tracker",
        gmail_max_results=50,
        scoring_rules_path=RULES_PATH,
    )
    monkeypatch.setattr(gmail_ingestion, "load_settings", lambda: settings)

    result = gmail_ingestion.run_gmail_ingestion(force_reprocess=True)

    assert result["status"] == "systemic_failure"
    assert result["systemic_failure_category"] == "configuration"
    assert result["ingestion_run_recorded"] is False


def test_invalid_token_failure_is_categorized_and_run_record_is_written(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService({})
    _patch_runner(monkeypatch, sheet_client, service)
    monkeypatch.setattr(
        gmail_ingestion,
        "build_gmail_service",
        lambda client, token: (_ for _ in ()).throw(GmailAuthenticationError("Gmail token is invalid")),
    )

    result = gmail_ingestion.run_gmail_ingestion()

    assert result["status"] == "systemic_failure"
    assert result["systemic_failure_category"] == "authentication"
    assert result["systemic_failure_stage"] == "authentication"
    assert result["ingestion_run_recorded"] is True
    assert len(sheet_client.tables["Runs"]) == 1
    assert sheet_client.tables["Runs"][0]["status"] == "systemic_failure"
