from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from src.data_quality import filter_jobs_for_upsert, rejected_job_record
from src.job_upsert import upsert_jobs
from src.models import today_iso, utc_now_iso
from src.schema import GMAIL_MESSAGES_HEADERS, REJECTED_JOBS_HEADERS, SchemaValidationError, compare_headers, HeaderSpec
from src.scoring import load_scoring_rules
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff
from src.sources.gmail_alerts import (
    DEFAULT_GMAIL_LABEL_NAME,
    GmailAlertEmail,
    alert_to_rejected_job_record,
    build_gmail_service,
    find_gmail_label_id,
    gmail_message_to_email,
    parse_job_alert_email,
    parsed_alerts_to_jobs,
    should_upsert_alert,
)

GMAIL_MESSAGES_WORKSHEET = "Gmail_Messages"
REJECTED_JOBS_WORKSHEET = "Rejected_Jobs"
COMPLETED_MESSAGE_STATUSES = {"success", "no_jobs", "permanent_failure"}
RETRYABLE_MESSAGE_STATUSES = {"retryable_failure"}
SUPPORTED_MESSAGE_STATUSES = COMPLETED_MESSAGE_STATUSES | RETRYABLE_MESSAGE_STATUSES


@dataclass(slots=True)
class GmailListBatch:
    message_refs: list[dict[str, str]]
    pages_fetched: int
    result_size_estimate: int
    messages_already_processed: int


@dataclass(slots=True)
class GmailIngestionSummary:
    status: str
    gmail_label_name: str
    messages_fetched: int = 0
    pages_fetched: int = 0
    messages_already_processed: int = 0
    new_messages_processed: int = 0
    failed_messages: int = 0
    no_jobs_messages: int = 0
    backlog_remaining: int = 0
    jobs_accepted: int = 0
    alerts_rejected: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    duplicates_matched: int = 0
    force_reprocess: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            {
                "run_mode": "gmail_ingestion_reliability",
                "emails_read": self.messages_fetched,
                "jobs_found": self.jobs_accepted,
                "rejected_alerts": self.alerts_rejected,
                "final_gate_rejected_jobs": 0,
                "quarantined_alerts": self.alerts_rejected,
            }
        )
        return data


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _normalize_record(record: dict[str, Any], headers: Iterable[str]) -> dict[str, Any]:
    return {header: record.get(header, "") for header in headers}


def ensure_gmail_messages_worksheet(sheet_client: Any) -> None:
    worksheet = sheet_client.ensure_worksheet(
        GMAIL_MESSAGES_WORKSHEET,
        rows=1000,
        cols=len(GMAIL_MESSAGES_HEADERS),
    )
    current_headers = with_quota_backoff(
        lambda: worksheet.row_values(1),
        operation_name=f"read headers {GMAIL_MESSAGES_WORKSHEET}",
    )
    if not any(str(value).strip() for value in current_headers):
        end_column = _column_name(len(GMAIL_MESSAGES_HEADERS))
        with_quota_backoff(
            lambda: worksheet.update(
                range_name=f"A1:{end_column}1",
                values=[GMAIL_MESSAGES_HEADERS],
                value_input_option="USER_ENTERED",
            ),
            operation_name=f"initialize headers {GMAIL_MESSAGES_WORKSHEET}",
        )
        if hasattr(sheet_client, "_header_cache"):
            sheet_client._header_cache.pop(GMAIL_MESSAGES_WORKSHEET, None)
        return

    validation = compare_headers(
        HeaderSpec(GMAIL_MESSAGES_WORKSHEET, GMAIL_MESSAGES_HEADERS),
        current_headers,
    )
    if not validation.ok:
        raise SchemaValidationError(
            f"Worksheet {GMAIL_MESSAGES_WORKSHEET} headers do not match the canonical schema"
        )


def load_gmail_message_ledger(sheet_client: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    rows = sheet_client.read_records_with_row_numbers(GMAIL_MESSAGES_WORKSHEET)
    ledger: dict[str, tuple[int, dict[str, Any]]] = {}
    for row_number, record in rows:
        message_id = str(record.get("message_id") or "").strip()
        if message_id:
            ledger[message_id] = (row_number, _normalize_record(record, GMAIL_MESSAGES_HEADERS))
    return ledger


def completed_message_ids(ledger: dict[str, tuple[int, dict[str, Any]]]) -> set[str]:
    return {
        message_id
        for message_id, (_, record) in ledger.items()
        if str(record.get("status") or "").strip() in COMPLETED_MESSAGE_STATUSES
    }


def list_labeled_gmail_message_refs(
    service: Any,
    *,
    label_name: str = DEFAULT_GMAIL_LABEL_NAME,
    max_results: int = 50,
    query: str = "",
    completed_ids: set[str] | None = None,
    force_reprocess: bool = False,
) -> GmailListBatch:
    label_id = find_gmail_label_id(service, label_name)
    completed = completed_ids or set()
    refs: list[dict[str, str]] = []
    page_token = ""
    pages_fetched = 0
    result_size_estimate = 0

    while len(refs) < max_results:
        page_size = min(500, max_results - len(refs))
        request_kwargs: dict[str, Any] = {
            "userId": "me",
            "labelIds": [label_id],
            "maxResults": page_size,
        }
        if query:
            request_kwargs["q"] = query
        if page_token:
            request_kwargs["pageToken"] = page_token

        response = service.users().messages().list(**request_kwargs).execute()
        pages_fetched += 1
        result_size_estimate = max(
            result_size_estimate,
            _as_int(response.get("resultSizeEstimate"), 0),
        )
        for item in response.get("messages", []) or []:
            if len(refs) >= max_results:
                break
            message_id = str(item.get("id") or "").strip()
            if message_id:
                refs.append(
                    {
                        "id": message_id,
                        "threadId": str(item.get("threadId") or "").strip(),
                    }
                )

        page_token = str(response.get("nextPageToken") or "").strip()
        if not page_token:
            break

    already_processed = 0
    if not force_reprocess:
        already_processed = sum(1 for item in refs if item["id"] in completed)

    return GmailListBatch(
        message_refs=refs,
        pages_fetched=pages_fetched,
        result_size_estimate=max(result_size_estimate, len(refs)),
        messages_already_processed=already_processed,
    )


class RejectedRecordStore:
    def __init__(self, sheet_client: Any):
        self.sheet_client = sheet_client
        rows = sheet_client.read_records_with_row_numbers(REJECTED_JOBS_WORKSHEET)
        self.records: dict[str, tuple[int, dict[str, Any]]] = {}
        for row_number, record in rows:
            rejected_id = str(record.get("rejected_id") or "").strip()
            if rejected_id:
                self.records[rejected_id] = (
                    row_number,
                    _normalize_record(record, REJECTED_JOBS_HEADERS),
                )

    @staticmethod
    def _equivalent(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        ignored = {"updated_at"}
        return all(
            str(existing.get(key, "")) == str(incoming.get(key, ""))
            for key in REJECTED_JOBS_HEADERS
            if key not in ignored
        )

    def upsert(self, records: Iterable[dict[str, Any]]) -> int:
        unique_records: dict[str, dict[str, Any]] = {}
        for record in records:
            rejected_id = str(record.get("rejected_id") or "").strip()
            if rejected_id:
                unique_records[rejected_id] = _normalize_record(record, REJECTED_JOBS_HEADERS)

        appended = 0
        for rejected_id, incoming in unique_records.items():
            existing_entry = self.records.get(rejected_id)
            if existing_entry is not None:
                row_number, existing = existing_entry
                incoming["created_at"] = existing.get("created_at") or incoming.get("created_at")
                if not self._equivalent(existing, incoming):
                    self.sheet_client.update_record(REJECTED_JOBS_WORKSHEET, row_number, incoming)
                    self.records[rejected_id] = (row_number, incoming)
                continue

            self.sheet_client.append_record(REJECTED_JOBS_WORKSHEET, incoming)
            next_row = max((row for row, _ in self.records.values()), default=1) + 1
            self.records[rejected_id] = (next_row, incoming)
            appended += 1
        return appended


def _message_record(
    email: GmailAlertEmail,
    *,
    existing: dict[str, Any] | None,
    status: str,
    alerts_parsed: int,
    jobs_accepted: int,
    jobs_rejected: int,
    error_message: str = "",
) -> dict[str, Any]:
    if status not in SUPPORTED_MESSAGE_STATUSES:
        raise ValueError(f"Unsupported Gmail message status: {status}")
    now = utc_now_iso()
    existing_record = existing or {}
    return {
        "message_id": email.message_id,
        "thread_id": email.thread_id,
        "subject": email.subject,
        "sender": email.sender,
        "received_at": email.received_at,
        "status": status,
        "attempt_count": _as_int(existing_record.get("attempt_count"), 0) + 1,
        "alerts_parsed": alerts_parsed,
        "jobs_accepted": jobs_accepted,
        "jobs_rejected": jobs_rejected,
        "error_message": str(error_message or "")[:2000],
        "first_processed_at": existing_record.get("first_processed_at") or now,
        "last_processed_at": now,
    }


def upsert_gmail_message_record(
    sheet_client: Any,
    ledger: dict[str, tuple[int, dict[str, Any]]],
    record: dict[str, Any],
) -> None:
    message_id = str(record.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Gmail ledger records require message_id")
    existing_entry = ledger.get(message_id)
    if existing_entry is not None:
        row_number, _ = existing_entry
        sheet_client.update_record(GMAIL_MESSAGES_WORKSHEET, row_number, record)
        ledger[message_id] = (row_number, record)
        return

    sheet_client.append_record(GMAIL_MESSAGES_WORKSHEET, record)
    next_row = max((row for row, _ in ledger.values()), default=1) + 1
    ledger[message_id] = (next_row, record)


def _failure_status(error: Exception) -> str:
    if isinstance(error, (UnicodeError, ValueError)):
        return "permanent_failure"
    return "retryable_failure"


def _final_gate_rejected_records(email: GmailAlertEmail, rejections: Iterable[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for rejection in rejections:
        record = rejected_job_record(rejection)
        record.update(
            {
                "message_id": email.message_id,
                "thread_id": email.thread_id,
                "subject": email.subject,
                "sender": email.sender,
            }
        )
        records.append(record)
    return records


def build_gmail_ingestion_run_record(summary: GmailIngestionSummary) -> dict[str, Any]:
    now = utc_now_iso()
    timestamp = now.replace(":", "").replace("-", "").replace("+00:00", "Z")
    return {
        "run_id": f"gmail_ingestion_{timestamp}",
        "run_type": "gmail_ingestion_reliability",
        "source_type": "gmail_alert",
        "source_name": summary.gmail_label_name,
        "status": summary.status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": summary.jobs_accepted,
        "records_inserted": summary.jobs_created,
        "records_updated": summary.jobs_updated,
        "records_failed": summary.failed_messages,
        "rows_read": summary.messages_fetched,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": "All eligible Gmail messages failed" if summary.status == "all_new_messages_failed" else "",
        "notes": json.dumps(summary.to_dict(), sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def run_gmail_ingestion(*, force_reprocess: bool = False) -> dict[str, Any]:
    settings = load_settings()
    if not settings.gmail_client_config:
        raise ValueError("GMAIL_CLIENT_CONFIG is required for Gmail alert ingestion")
    if not settings.gmail_token_json:
        raise ValueError("GMAIL_TOKEN_JSON is required for Gmail alert ingestion")

    sheet_client = SheetClient.from_settings(settings)
    ensure_gmail_messages_worksheet(sheet_client)
    ledger = load_gmail_message_ledger(sheet_client)
    rejected_store = RejectedRecordStore(sheet_client)
    rules = load_scoring_rules(settings.scoring_rules_path)
    service = build_gmail_service(settings.gmail_client_config, settings.gmail_token_json)

    batch = list_labeled_gmail_message_refs(
        service,
        label_name=settings.gmail_label_name,
        max_results=settings.gmail_max_results,
        completed_ids=completed_message_ids(ledger),
        force_reprocess=force_reprocess,
    )
    summary = GmailIngestionSummary(
        status="success",
        gmail_label_name=settings.gmail_label_name,
        messages_fetched=len(batch.message_refs),
        pages_fetched=batch.pages_fetched,
        messages_already_processed=batch.messages_already_processed,
        force_reprocess=force_reprocess,
    )

    eligible_refs = [
        item
        for item in batch.message_refs
        if force_reprocess or item["id"] not in completed_message_ids(ledger)
    ]
    completed_this_run = 0

    for item in eligible_refs:
        message_id = item["id"]
        existing_entry = ledger.get(message_id)
        existing = existing_entry[1] if existing_entry else None
        email = GmailAlertEmail(message_id=message_id, thread_id=item.get("threadId", ""))
        alerts_parsed = 0
        jobs_accepted = 0
        jobs_rejected = 0
        try:
            message = service.users().messages().get(
                userId="me",
                id=message_id,
                format="full",
            ).execute()
            email = gmail_message_to_email(message)
            alerts = parse_job_alert_email(email)
            alerts_parsed = len(alerts)

            parser_rejections = [
                alert_to_rejected_job_record(alert)
                for alert in alerts
                if not should_upsert_alert(alert)
            ]
            candidate_jobs = parsed_alerts_to_jobs(
                alerts,
                scoring_rules=rules,
                seen_date=today_iso(),
            )
            accepted_jobs, final_gate_rejections = filter_jobs_for_upsert(candidate_jobs)
            final_rejections = _final_gate_rejected_records(email, final_gate_rejections)
            rejected_records = parser_rejections + final_rejections
            jobs_accepted = len(accepted_jobs)
            jobs_rejected = len(rejected_records)

            if rejected_records:
                rejected_store.upsert(rejected_records)
            upsert_summary = upsert_jobs(
                sheet_client,
                accepted_jobs,
                seen_date=today_iso(),
            )
            upsert_data = upsert_summary.to_dict()
            summary.jobs_created += _as_int(upsert_data.get("jobs_created"), 0)
            summary.jobs_updated += _as_int(upsert_data.get("jobs_updated"), 0)
            summary.duplicates_matched += _as_int(upsert_data.get("duplicates_matched"), 0)
            summary.jobs_accepted += jobs_accepted
            summary.alerts_rejected += jobs_rejected

            status = "success" if jobs_accepted else "no_jobs"
            record = _message_record(
                email,
                existing=existing,
                status=status,
                alerts_parsed=alerts_parsed,
                jobs_accepted=jobs_accepted,
                jobs_rejected=jobs_rejected,
            )
            upsert_gmail_message_record(sheet_client, ledger, record)
            summary.new_messages_processed += 1
            completed_this_run += 1
            if status == "no_jobs":
                summary.no_jobs_messages += 1
        except Exception as error:
            failure_status = _failure_status(error)
            failure_record = _message_record(
                email,
                existing=existing,
                status=failure_status,
                alerts_parsed=alerts_parsed,
                jobs_accepted=jobs_accepted,
                jobs_rejected=jobs_rejected,
                error_message=f"{type(error).__name__}: {error}",
            )
            try:
                upsert_gmail_message_record(sheet_client, ledger, failure_record)
            except Exception as ledger_error:
                raise RuntimeError(
                    f"Unable to record Gmail failure for message {message_id}: {ledger_error}"
                ) from error
            summary.failed_messages += 1

    unlisted_backlog = max(0, batch.result_size_estimate - len(batch.message_refs))
    summary.backlog_remaining = unlisted_backlog + sum(
        1
        for item in eligible_refs
        if str((ledger.get(item["id"]) or (0, {}))[1].get("status") or "") == "retryable_failure"
    )

    if not batch.message_refs:
        summary.status = "no_labeled_emails"
    elif not eligible_refs:
        summary.status = "no_new_messages"
    elif summary.failed_messages == len(eligible_refs):
        summary.status = "all_new_messages_failed"
    elif summary.failed_messages:
        summary.status = "partial_failure"
    else:
        summary.status = "success"

    sheet_client.append_run(build_gmail_ingestion_run_record(summary))
    return summary.to_dict()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reliable Gmail job alert ingestion")
    parser.add_argument("--run", action="store_true", help="Run Gmail ingestion")
    parser.add_argument(
        "--ensure-ledger",
        action="store_true",
        help="Create and initialize the Gmail_Messages worksheet",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Reprocess messages even when the ledger already marks them complete",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.ensure_ledger:
        sheet_client = SheetClient.from_settings(settings)
        ensure_gmail_messages_worksheet(sheet_client)
        print(json.dumps({"status": "success", "worksheet": GMAIL_MESSAGES_WORKSHEET}, indent=2))
        return

    result = run_gmail_ingestion(force_reprocess=args.force_reprocess)
    print(json.dumps(result, indent=2))
    if result.get("status") == "all_new_messages_failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
