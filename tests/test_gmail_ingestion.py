import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.gmail_ingestion as gmail_ingestion
from src.gmail_ingestion import (
    RejectedRecordStore,
    _message_record,
    completed_message_ids,
    list_labeled_gmail_message_refs,
    run_gmail_ingestion,
    upsert_gmail_message_record,
)
from src.schema import GMAIL_MESSAGES_HEADERS, REJECTED_JOBS_HEADERS
from src.sources.eml import read_eml
from src.sources.gmail_alerts import GmailAlertEmail


FIXTURES = Path(__file__).parent / "fixtures"
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
        self.list_calls = []
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
        self.list_calls.append(kwargs)
        refs = [{"id": message_id, "threadId": f"thread-{message_id}"} for message_id in self.ordered_ids]
        return FakeRequest({"messages": refs, "resultSizeEstimate": len(refs)})

    def get(self, **kwargs):
        message_id = kwargs["id"]
        self.get_calls.append(message_id)
        payload = self.message_payloads[message_id]
        if isinstance(payload, Exception):
            return FakeRequest(error=payload)
        return FakeRequest(payload)


class PaginatedFakeGmailService(FakeGmailService):
    def __init__(self):
        super().__init__()

    def list(self, **kwargs):
        if "labelIds" not in kwargs:
            return FakeRequest({"labels": [{"id": "Label_1", "name": "Job Tracker"}]})
        self.list_calls.append(kwargs)
        if not kwargs.get("pageToken"):
            return FakeRequest(
                {
                    "messages": [
                        {"id": "m1", "threadId": "t1"},
                        {"id": "m2", "threadId": "t2"},
                    ],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 4,
                }
            )
        return FakeRequest(
            {
                "messages": [
                    {"id": "m3", "threadId": "t3"},
                    {"id": "m4", "threadId": "t4"},
                ],
                "resultSizeEstimate": 4,
            }
        )


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
            "Rejected_Jobs": [],
            "Jobs": [],
            "Job_Sources": [],
            "Runs": [],
        }
        self.headers = {}
        self.append_calls = []
        self.update_calls = []
        self.read_counts = {}
        self._header_cache = {}

    def ensure_worksheet(self, worksheet_name, *, rows, cols):
        del rows, cols
        self.tables.setdefault(worksheet_name, [])
        return FakeWorksheet(self, worksheet_name)

    def read_records_with_row_numbers(self, worksheet_name):
        self.read_counts[worksheet_name] = self.read_counts.get(worksheet_name, 0) + 1
        return [(index + 2, dict(record)) for index, record in enumerate(self.tables[worksheet_name])]

    def append_record(self, worksheet_name, record):
        self.append_calls.append((worksheet_name, dict(record)))
        self.tables[worksheet_name].append(dict(record))

    def append_records(self, worksheet_name, records):
        for record in records:
            self.append_record(worksheet_name, record)

    def update_record(self, worksheet_name, row_number, record):
        self.update_calls.append((worksheet_name, row_number, dict(record)))
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


def _gmail_api_message(email, message_id):
    parts = []
    if email.body_text:
        parts.append({"mimeType": "text/plain", "body": {"data": _encoded(email.body_text)}})
    if email.body_html:
        parts.append({"mimeType": "text/html", "body": {"data": _encoded(email.body_html)}})
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "payload": {
            "mimeType": "multipart/alternative" if len(parts) > 1 else parts[0]["mimeType"],
            "headers": [
                {"name": "Subject", "value": email.subject},
                {"name": "From", "value": email.sender},
                {"name": "Date", "value": email.received_at},
            ],
            "parts": parts,
        },
    }


def _fixture_message(filename, message_id):
    return _gmail_api_message(read_eml(FIXTURES / filename), message_id)


def _single_job_message(message_id="single"):
    email = GmailAlertEmail(
        message_id=message_id,
        subject="Director, Business Operations at Acme",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Mon, 22 Jun 2026 10:44:27 +0000",
        body_text="New jobs match your preferences\nhttps://www.linkedin.com/jobs/view/6000000001/?trackingId=single",
    )
    return _gmail_api_message(email, message_id)


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


def test_gmail_listing_paginates_and_processes_past_completed_messages():
    service = PaginatedFakeGmailService()

    batch = list_labeled_gmail_message_refs(
        service,
        label_name="Job Tracker",
        max_results=3,
        completed_ids={"m1"},
    )

    assert [item["id"] for item in batch.message_refs] == ["m2", "m3", "m4"]
    assert batch.pending_message_ids == ["m2", "m3", "m4"]
    assert batch.messages_listed == 4
    assert batch.pages_fetched == 2
    assert batch.result_size_estimate == 4
    assert batch.messages_already_processed == 1
    assert service.list_calls[0]["maxResults"] == 500
    assert service.list_calls[1]["pageToken"] == "page-2"


def test_gmail_listing_caps_full_message_processing_after_listing_backlog():
    service = PaginatedFakeGmailService()

    batch = list_labeled_gmail_message_refs(service, label_name="Job Tracker", max_results=2)

    assert [item["id"] for item in batch.message_refs] == ["m1", "m2"]
    assert batch.pending_message_ids == ["m1", "m2", "m3", "m4"]


def test_completed_messages_skip_success_and_no_jobs_but_retry_failures():
    ledger = {
        "success": (2, {"status": "success"}),
        "empty": (3, {"status": "no_jobs"}),
        "failed": (4, {"status": "retryable_failure"}),
    }

    assert completed_message_ids(ledger) == {"success", "empty"}
    assert "failed" not in completed_message_ids(ledger)


def test_gmail_message_attempt_count_increments_on_retry():
    email = GmailAlertEmail(
        message_id="m1",
        thread_id="t1",
        subject="Job alert",
        sender="sender@example.com",
        received_at="2026-06-22T08:00:00-05:00",
    )

    record = _message_record(
        email,
        existing={"attempt_count": "2", "first_processed_at": "2026-06-20T00:00:00+00:00"},
        status="success",
        alerts_parsed=2,
        jobs_accepted=1,
        jobs_rejected=1,
    )

    assert record["attempt_count"] == 3
    assert record["first_processed_at"] == "2026-06-20T00:00:00+00:00"


def test_rejected_records_are_idempotent_by_rejected_id():
    sheet_client = FakeSheetClient()
    store = RejectedRecordStore(sheet_client)
    record = {header: "" for header in REJECTED_JOBS_HEADERS}
    record.update(
        {
            "rejected_id": "rejected-1",
            "rejection_reason": "utility_url",
            "created_at": "2026-06-22T12:00:00+00:00",
            "updated_at": "2026-06-22T12:00:00+00:00",
        }
    )

    assert store.upsert([record]) == 1
    assert store.upsert([record]) == 0
    assert len(sheet_client.tables["Rejected_Jobs"]) == 1


def test_gmail_message_ledger_updates_existing_row_instead_of_appending_duplicate():
    sheet_client = FakeSheetClient()
    existing = {header: "" for header in GMAIL_MESSAGES_HEADERS}
    existing.update({"message_id": "m1", "status": "retryable_failure", "attempt_count": 1})
    sheet_client.tables["Gmail_Messages"].append(existing)
    ledger = {"m1": (2, dict(existing))}
    replacement = dict(existing)
    replacement.update({"status": "success", "attempt_count": 2})

    upsert_gmail_message_record(sheet_client, ledger, replacement)

    assert len(sheet_client.tables["Gmail_Messages"]) == 1
    assert sheet_client.tables["Gmail_Messages"][0]["status"] == "success"
    assert len(sheet_client.update_calls) == 1


def test_full_runner_processes_topgolf_and_toyota_with_one_jobs_and_sources_read(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService(
        {
            "topgolf": _fixture_message("linkedin_topgolf.eml", "topgolf"),
            "toyota": _fixture_message("linkedin_toyota.eml", "toyota"),
        }
    )
    _patch_runner(monkeypatch, sheet_client, service)

    result = run_gmail_ingestion()

    assert result["status"] == "success"
    assert result["new_messages_processed"] == 2
    assert result["jobs_accepted"] == 12
    assert result["failed_messages"] == 0
    assert sheet_client.read_counts["Jobs"] == 1
    assert sheet_client.read_counts["Job_Sources"] == 1
    ledger = {row["message_id"]: row for row in sheet_client.tables["Gmail_Messages"]}
    assert ledger["topgolf"]["status"] == "success"
    assert ledger["toyota"]["status"] == "success"

    jobs = {(row["company"], row["title"]): row for row in sheet_client.tables["Jobs"]}
    topgolf = jobs[("Topgolf", "Sr Manager, Strategic Planning")]
    toyota = jobs[("Toyota North America", "National Manager, Product")]
    assert topgolf["canonical_url"] == "https://www.linkedin.com/jobs/view/4417965465"
    assert toyota["canonical_url"] == "https://www.linkedin.com/jobs/view/4430066274"
    assert "manual_review=true" in topgolf["score_explanation"]
    assert "manual_review=true" in toyota["score_explanation"]


def test_full_runner_records_no_jobs_and_skips_completed_message_on_second_run(monkeypatch):
    sheet_client = FakeSheetClient()
    empty_email = GmailAlertEmail(
        message_id="empty",
        subject="Your job alert has been created",
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
        received_at="Mon, 22 Jun 2026 10:44:27 +0000",
        body_text="You will receive notifications when new jobs are posted that match your search preferences.",
    )
    service = FakeGmailService({"empty": _gmail_api_message(empty_email, "empty")})
    _patch_runner(monkeypatch, sheet_client, service)

    first = run_gmail_ingestion()
    second = run_gmail_ingestion()

    assert first["status"] == "success"
    assert first["no_jobs_messages"] == 1
    assert sheet_client.tables["Gmail_Messages"][0]["status"] == "no_jobs"
    assert second["status"] == "no_new_messages"
    assert second["new_messages_processed"] == 0
    assert second["messages_already_processed"] == 1
    assert service.get_calls == ["empty"]


def test_full_runner_isolates_failure_and_retries_it_on_next_run(monkeypatch):
    sheet_client = FakeSheetClient()
    first_service = FakeGmailService(
        {
            "good": _single_job_message("good"),
            "bad": RuntimeError("temporary Gmail error"),
        },
        ordered_ids=["good", "bad"],
    )
    _patch_runner(monkeypatch, sheet_client, first_service)

    first = run_gmail_ingestion()

    assert first["status"] == "partial_failure"
    assert first["new_messages_processed"] == 1
    assert first["failed_messages"] == 1
    assert first["backlog_remaining"] == 1
    ledger = {row["message_id"]: row for row in sheet_client.tables["Gmail_Messages"]}
    assert ledger["good"]["status"] == "success"
    assert ledger["bad"]["status"] == "retryable_failure"
    assert ledger["bad"]["attempt_count"] == 1

    second_service = FakeGmailService(
        {
            "good": _single_job_message("good"),
            "bad": _single_job_message("bad"),
        },
        ordered_ids=["good", "bad"],
    )
    monkeypatch.setattr(gmail_ingestion, "build_gmail_service", lambda client, token: second_service)
    second = run_gmail_ingestion()

    assert second["status"] == "success"
    assert second["messages_already_processed"] == 1
    assert second["new_messages_processed"] == 1
    assert second["failed_messages"] == 0
    assert second["backlog_remaining"] == 0
    ledger = {row["message_id"]: row for row in sheet_client.tables["Gmail_Messages"]}
    assert ledger["bad"]["status"] == "success"
    assert ledger["bad"]["attempt_count"] == 2
    assert second_service.get_calls == ["bad"]


def test_full_runner_reports_all_failed_and_backlog(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService(
        {
            "bad-1": RuntimeError("temporary one"),
            "bad-2": RuntimeError("temporary two"),
        }
    )
    _patch_runner(monkeypatch, sheet_client, service)

    result = run_gmail_ingestion()

    assert result["status"] == "all_new_messages_failed"
    assert result["failed_messages"] == 2
    assert result["backlog_remaining"] == 2
    assert {row["status"] for row in sheet_client.tables["Gmail_Messages"]} == {"retryable_failure"}


def test_full_runner_respects_processing_cap_and_reports_remaining_backlog(monkeypatch):
    sheet_client = FakeSheetClient()
    service = FakeGmailService(
        {
            "first": _single_job_message("first"),
            "second": _single_job_message("second"),
        },
        ordered_ids=["first", "second"],
    )
    _patch_runner(monkeypatch, sheet_client, service, max_results=1)

    result = run_gmail_ingestion()

    assert result["status"] == "success"
    assert result["messages_fetched"] == 1
    assert result["new_messages_processed"] == 1
    assert result["backlog_remaining"] == 1
    assert service.get_calls == ["first"]
