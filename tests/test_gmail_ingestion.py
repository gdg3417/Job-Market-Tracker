from src.gmail_ingestion import (
    RejectedRecordStore,
    _message_record,
    completed_message_ids,
    list_labeled_gmail_message_refs,
    upsert_gmail_message_record,
)
from src.schema import GMAIL_MESSAGES_HEADERS, REJECTED_JOBS_HEADERS
from src.sources.gmail_alerts import GmailAlertEmail


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeGmailService:
    def __init__(self):
        self.list_calls = []

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


class FakeSheetClient:
    def __init__(self):
        self.tables = {
            "Gmail_Messages": [],
            "Rejected_Jobs": [],
        }
        self.append_calls = []
        self.update_calls = []

    def read_records_with_row_numbers(self, worksheet_name):
        return [
            (index + 2, dict(record))
            for index, record in enumerate(self.tables[worksheet_name])
        ]

    def append_record(self, worksheet_name, record):
        self.append_calls.append((worksheet_name, dict(record)))
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.update_calls.append((worksheet_name, row_number, dict(record)))
        self.tables[worksheet_name][row_number - 2] = dict(record)


def test_gmail_listing_paginates_and_processes_past_completed_messages():
    service = FakeGmailService()

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
    service = FakeGmailService()

    batch = list_labeled_gmail_message_refs(
        service,
        label_name="Job Tracker",
        max_results=2,
    )

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
