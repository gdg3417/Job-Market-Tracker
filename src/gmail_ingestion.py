from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from src.data_quality import filter_jobs_for_upsert, rejected_job_record
from src.gmail_diagnostics import (
    GmailFailureStore,
    FailureDiagnostic,
    build_failure_record,
    build_noninteractive_gmail_service,
    classify_failure,
    detect_systemic_failure,
    ensure_gmail_failures_worksheet,
    execute_with_bounded_retry,
    failure_signature,
)
from src.job_upsert import upsert_jobs
from src.models import today_iso, utc_now_iso
from src.schema import GMAIL_MESSAGES_HEADERS, REJECTED_JOBS_HEADERS, HeaderSpec, SchemaValidationError, compare_headers
from src.scoring import load_scoring_rules
from src.settings import load_settings
from src.sheets import SheetClient, with_quota_backoff
from src.sources.gmail_alerts import (
    DEFAULT_GMAIL_LABEL_NAME,
    GmailAlertEmail,
    alert_to_rejected_job_record,
    find_gmail_label_id,
    gmail_message_to_email,
    parse_job_alert_email,
    parsed_alerts_to_jobs,
    should_upsert_alert,
)

GMAIL_MESSAGES_WORKSHEET = "Gmail_Messages"
REJECTED_JOBS_WORKSHEET = "Rejected_Jobs"
COMPLETED_MESSAGE_STATUSES = {"success", "no_jobs", "permanent_failure", "quarantined"}
RETRYABLE_MESSAGE_STATUSES = {"retryable_failure"}
SUPPORTED_MESSAGE_STATUSES = COMPLETED_MESSAGE_STATUSES | RETRYABLE_MESSAGE_STATUSES
DEFAULT_MAX_MESSAGE_ATTEMPTS = 3

# Preserve the existing patch point used by tests and local callers.
build_gmail_service = build_noninteractive_gmail_service


@dataclass(slots=True)
class GmailListBatch:
    message_refs: list[dict[str, str]]
    pending_message_ids: list[str]
    messages_listed: int
    pages_fetched: int
    result_size_estimate: int
    messages_already_processed: int
    messages_not_selected: int = 0


@dataclass(slots=True)
class GmailIngestionSummary:
    status: str
    gmail_label_name: str
    messages_fetched: int = 0
    messages_listed: int = 0
    pages_fetched: int = 0
    messages_already_processed: int = 0
    messages_not_selected: int = 0
    new_messages_processed: int = 0
    processing_failures: int = 0
    failed_messages: int = 0
    quarantined_messages: int = 0
    no_jobs_messages: int = 0
    backlog_remaining: int = 0
    jobs_accepted: int = 0
    alerts_rejected: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    duplicates_matched: int = 0
    force_reprocess: bool = False
    replay_mode: str = "normal"
    selected_message_count: int = 0
    max_message_attempts: int = DEFAULT_MAX_MESSAGE_ATTEMPTS
    systemic_failure_category: str = ""
    systemic_failure_stage: str = ""
    systemic_failure_fingerprint: str = ""
    ingestion_run_recorded: bool = False
    diagnostics_recorded: bool = True
    diagnostic_write_failures: int = 0
    run_error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            {
                "run_mode": "gmail_ingestion_reliability",
                "emails_read": self.messages_fetched,
                "successfully_processed": self.new_messages_processed,
                "jobs_found": self.jobs_accepted,
                "rejected_alerts": self.alerts_rejected,
                "final_gate_rejected_jobs": 0,
                "quarantined_alerts": self.alerts_rejected,
            }
        )
        return data


@dataclass(slots=True)
class MessageFailureContext:
    email: GmailAlertEmail
    existing: dict[str, Any] | None
    alerts_parsed: int
    jobs_accepted: int
    jobs_rejected: int
    diagnostic: FailureDiagnostic

    @property
    def attempt_count(self) -> int:
        return _as_int((self.existing or {}).get("attempt_count"), 0) + 1


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


def _normalize_message_ids(message_ids: Sequence[str] | None) -> set[str]:
    normalized: set[str] = set()
    for value in message_ids or []:
        for item in str(value or "").split(","):
            message_id = item.strip()
            if message_id:
                normalized.add(message_id)
    return normalized


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


def retryable_message_ids(ledger: dict[str, tuple[int, dict[str, Any]]]) -> set[str]:
    return {
        message_id
        for message_id, (_, record) in ledger.items()
        if str(record.get("status") or "").strip() in RETRYABLE_MESSAGE_STATUSES
    }


def list_labeled_gmail_message_refs(
    service: Any,
    *,
    label_name: str = DEFAULT_GMAIL_LABEL_NAME,
    max_results: int = 50,
    query: str = "",
    completed_ids: set[str] | None = None,
    eligible_ids: set[str] | None = None,
    force_reprocess: bool = False,
) -> GmailListBatch:
    label_id = execute_with_bounded_retry(
        lambda: find_gmail_label_id(service, label_name),
        stage="list_messages",
    )
    completed = completed_ids or set()
    pending_refs: list[dict[str, str]] = []
    pending_message_ids: list[str] = []
    page_token = ""
    pages_fetched = 0
    result_size_estimate = 0
    messages_listed = 0
    messages_already_processed = 0
    messages_not_selected = 0

    while True:
        request_kwargs: dict[str, Any] = {
            "userId": "me",
            "labelIds": [label_id],
            "maxResults": 500,
        }
        if query:
            request_kwargs["q"] = query
        if page_token:
            request_kwargs["pageToken"] = page_token

        response = execute_with_bounded_retry(
            lambda: service.users().messages().list(**request_kwargs).execute(),
            stage="list_messages",
        )
        pages_fetched += 1
        result_size_estimate = max(
            result_size_estimate,
            _as_int(response.get("resultSizeEstimate"), 0),
        )
        for item in response.get("messages", []) or []:
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                continue
            messages_listed += 1
            selected_by_filter = eligible_ids is None or message_id in eligible_ids
            if message_id in completed and not (force_reprocess and selected_by_filter):
                messages_already_processed += 1
                continue

            pending_message_ids.append(message_id)
            if not selected_by_filter:
                messages_not_selected += 1
                continue
            pending_refs.append(
                {
                    "id": message_id,
                    "threadId": str(item.get("threadId") or "").strip(),
                }
            )

        page_token = str(response.get("nextPageToken") or "").strip()
        if not page_token:
            break

    selected_refs = pending_refs[:max_results]
    return GmailListBatch(
        message_refs=selected_refs,
        pending_message_ids=pending_message_ids,
        messages_listed=messages_listed,
        pages_fetched=pages_fetched,
        result_size_estimate=max(result_size_estimate, messages_listed),
        messages_already_processed=messages_already_processed,
        messages_not_selected=messages_not_selected,
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
    diagnostic = classify_failure(error, stage="parse_message")
    return "retryable_failure" if diagnostic.retry_eligible else "permanent_failure"


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
    error_message = summary.run_error_message
    if not error_message and summary.systemic_failure_category:
        error_message = (
            f"Systemic {summary.systemic_failure_category} failure"
            f" at {summary.systemic_failure_stage or 'unknown_stage'}"
        )
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
        "records_failed": max(summary.processing_failures, 1 if summary.status == "systemic_failure" else 0),
        "rows_read": summary.messages_fetched,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": error_message,
        "notes": json.dumps(summary.to_dict(), sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def _set_systemic_summary(summary: GmailIngestionSummary, diagnostic: FailureDiagnostic) -> None:
    summary.systemic_failure_category = diagnostic.category
    summary.systemic_failure_stage = diagnostic.stage
    summary.systemic_failure_fingerprint = diagnostic.fingerprint
    summary.run_error_message = diagnostic.message


def _record_message_failures(
    *,
    sheet_client: Any,
    ledger: dict[str, tuple[int, dict[str, Any]]],
    failure_store: GmailFailureStore,
    contexts: list[MessageFailureContext],
    summary: GmailIngestionSummary,
    max_message_attempts: int,
    systemic_diagnostic: FailureDiagnostic | None,
) -> None:
    systemic_signature = failure_signature(systemic_diagnostic) if systemic_diagnostic else None
    for context in contexts:
        diagnostic = context.diagnostic
        is_systemic = systemic_signature is not None and failure_signature(diagnostic) == systemic_signature
        quarantine = not is_systemic and (
            not diagnostic.retry_eligible or context.attempt_count >= max_message_attempts
        )
        status = "quarantined" if quarantine else "retryable_failure"
        retry_eligible = not quarantine
        message_record = _message_record(
            context.email,
            existing=context.existing,
            status=status,
            alerts_parsed=context.alerts_parsed,
            jobs_accepted=context.jobs_accepted,
            jobs_rejected=context.jobs_rejected,
            error_message=diagnostic.message,
        )
        try:
            upsert_gmail_message_record(sheet_client, ledger, message_record)
        except Exception:
            summary.diagnostics_recorded = False
            summary.diagnostic_write_failures += 1

        failure_record = build_failure_record(
            context.email,
            attempt_count=context.attempt_count,
            diagnostic=diagnostic,
            retry_eligible=retry_eligible,
            systemic_failure=is_systemic,
            status=status,
        )
        try:
            failure_store.upsert(failure_record)
        except Exception:
            summary.diagnostics_recorded = False
            summary.diagnostic_write_failures += 1

        if quarantine:
            summary.quarantined_messages += 1
        else:
            summary.failed_messages += 1


def _backlog_remaining(
    pending_message_ids: Iterable[str],
    ledger: dict[str, tuple[int, dict[str, Any]]],
) -> int:
    return sum(
        1
        for message_id in pending_message_ids
        if str((ledger.get(message_id) or (0, {}))[1].get("status") or "") not in COMPLETED_MESSAGE_STATUSES
    )


def run_gmail_ingestion(
    *,
    force_reprocess: bool = False,
    retry_failed_only: bool = False,
    message_ids: Sequence[str] | None = None,
    max_message_attempts: int = DEFAULT_MAX_MESSAGE_ATTEMPTS,
) -> dict[str, Any]:
    settings = load_settings()
    selected_ids = _normalize_message_ids(message_ids)
    max_message_attempts = max(1, int(max_message_attempts))
    replay_mode = "selected" if selected_ids else "failed_only" if retry_failed_only else "normal"
    summary = GmailIngestionSummary(
        status="starting",
        gmail_label_name=getattr(settings, "gmail_label_name", DEFAULT_GMAIL_LABEL_NAME),
        force_reprocess=force_reprocess,
        replay_mode=replay_mode,
        selected_message_count=len(selected_ids),
        max_message_attempts=max_message_attempts,
    )
    sheet_client: Any | None = None
    stage = "configuration"

    try:
        if force_reprocess and not selected_ids:
            raise ValueError("Force reprocessing requires one or more explicit message IDs")
        if retry_failed_only and selected_ids:
            raise ValueError("Use either failed-only replay or selected message replay, not both")
        if not settings.gmail_client_config:
            raise ValueError("GMAIL_CLIENT_CONFIG is required for Gmail alert ingestion")
        if not settings.gmail_token_json:
            raise ValueError("GMAIL_TOKEN_JSON is required for Gmail alert ingestion")

        stage = "workbook_connect"
        sheet_client = SheetClient.from_settings(settings)
        stage = "workbook_schema"
        ensure_gmail_messages_worksheet(sheet_client)
        ensure_gmail_failures_worksheet(sheet_client)
        ledger = load_gmail_message_ledger(sheet_client)
        failure_store = GmailFailureStore(sheet_client)
        rejected_store = RejectedRecordStore(sheet_client)
        rules = load_scoring_rules(settings.scoring_rules_path)

        stage = "authentication"
        service = build_gmail_service(settings.gmail_client_config, settings.gmail_token_json)
        eligible_ids: set[str] | None = None
        if retry_failed_only:
            eligible_ids = retryable_message_ids(ledger)
        elif selected_ids:
            eligible_ids = selected_ids

        stage = "list_messages"
        batch = list_labeled_gmail_message_refs(
            service,
            label_name=settings.gmail_label_name,
            max_results=settings.gmail_max_results,
            completed_ids=completed_message_ids(ledger),
            eligible_ids=eligible_ids,
            force_reprocess=force_reprocess,
        )
        summary.messages_fetched = len(batch.message_refs)
        summary.messages_listed = batch.messages_listed
        summary.pages_fetched = batch.pages_fetched
        summary.messages_already_processed = batch.messages_already_processed
        summary.messages_not_selected = batch.messages_not_selected

        eligible_refs = batch.message_refs
        failure_contexts: list[MessageFailureContext] = []
        for item in eligible_refs:
            message_id = item["id"]
            existing_entry = ledger.get(message_id)
            existing = existing_entry[1] if existing_entry else None
            email = GmailAlertEmail(message_id=message_id, thread_id=item.get("threadId", ""))
            alerts_parsed = 0
            jobs_accepted = 0
            jobs_rejected = 0
            message_stage = "retrieve_message"
            try:
                message = execute_with_bounded_retry(
                    lambda message_id=message_id: service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="full",
                    ).execute(),
                    stage=message_stage,
                )
                message_stage = "normalize_message"
                email = gmail_message_to_email(message)
                message_stage = "parse_message"
                alerts = parse_job_alert_email(email)
                alerts_parsed = len(alerts)

                parser_rejections = [
                    alert_to_rejected_job_record(alert)
                    for alert in alerts
                    if not should_upsert_alert(alert)
                ]
                message_stage = "deduplication"
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
                    message_stage = "write_rejections"
                    rejected_store.upsert(rejected_records)
                message_stage = "write_jobs"
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
                message_stage = "write_message_ledger"
                upsert_gmail_message_record(sheet_client, ledger, record)
                summary.new_messages_processed += 1
                if status == "no_jobs":
                    summary.no_jobs_messages += 1
            except Exception as error:
                failure_contexts.append(
                    MessageFailureContext(
                        email=email,
                        existing=existing,
                        alerts_parsed=alerts_parsed,
                        jobs_accepted=jobs_accepted,
                        jobs_rejected=jobs_rejected,
                        diagnostic=classify_failure(error, stage=message_stage),
                    )
                )
                summary.processing_failures += 1

        systemic_diagnostic = None
        if failure_contexts and len(failure_contexts) == len(eligible_refs):
            systemic_diagnostic = detect_systemic_failure(context.diagnostic for context in failure_contexts)
        if systemic_diagnostic is not None:
            _set_systemic_summary(summary, systemic_diagnostic)

        _record_message_failures(
            sheet_client=sheet_client,
            ledger=ledger,
            failure_store=failure_store,
            contexts=failure_contexts,
            summary=summary,
            max_message_attempts=max_message_attempts,
            systemic_diagnostic=systemic_diagnostic,
        )
        summary.backlog_remaining = _backlog_remaining(batch.pending_message_ids, ledger)

        if batch.messages_listed == 0:
            summary.status = "no_labeled_emails"
        elif not eligible_refs:
            summary.status = "no_new_messages"
        elif summary.failed_messages == len(eligible_refs):
            summary.status = "all_new_messages_failed"
        elif summary.failed_messages:
            summary.status = "partial_failure"
        elif summary.quarantined_messages:
            summary.status = "completed_with_quarantine"
        else:
            summary.status = "success"
    except Exception as error:
        diagnostic = classify_failure(error, stage=stage)
        _set_systemic_summary(summary, diagnostic)
        summary.status = "systemic_failure"
        summary.processing_failures = max(1, summary.processing_failures)
    finally:
        if sheet_client is not None:
            summary.ingestion_run_recorded = True
            try:
                sheet_client.append_run(build_gmail_ingestion_run_record(summary))
            except Exception as record_error:
                summary.ingestion_run_recorded = False
                diagnostic = classify_failure(record_error, stage="record_run")
                if not summary.systemic_failure_category:
                    _set_systemic_summary(summary, diagnostic)
                if summary.status not in {"systemic_failure", "all_new_messages_failed"}:
                    summary.status = "run_record_failure"
        else:
            summary.ingestion_run_recorded = False

    return summary.to_dict()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reliable Gmail job alert ingestion")
    parser.add_argument("--run", action="store_true", help="Run Gmail ingestion")
    parser.add_argument(
        "--ensure-ledger",
        action="store_true",
        help="Create and initialize Gmail message and failure worksheets",
    )
    parser.add_argument(
        "--retry-failed-only",
        action="store_true",
        help="Replay only messages currently marked retryable_failure",
    )
    parser.add_argument(
        "--message-id",
        action="append",
        default=[],
        help="Replay an exact Gmail message ID. Repeat the argument or provide comma-separated IDs.",
    )
    parser.add_argument(
        "--force-reprocess-selected",
        action="store_true",
        help="Allow explicitly selected completed messages to be replayed",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Deprecated alias for --force-reprocess-selected; explicit message IDs are required",
    )
    parser.add_argument(
        "--max-message-attempts",
        type=int,
        default=DEFAULT_MAX_MESSAGE_ATTEMPTS,
        help="Quarantine isolated message failures after this many ingestion attempts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.ensure_ledger:
        sheet_client = SheetClient.from_settings(settings)
        ensure_gmail_messages_worksheet(sheet_client)
        ensure_gmail_failures_worksheet(sheet_client)
        print(
            json.dumps(
                {
                    "status": "success",
                    "worksheets": [GMAIL_MESSAGES_WORKSHEET, "Gmail_Failures"],
                },
                indent=2,
            )
        )
        return

    result = run_gmail_ingestion(
        force_reprocess=args.force_reprocess_selected or args.force_reprocess,
        retry_failed_only=args.retry_failed_only,
        message_ids=args.message_id,
        max_message_attempts=args.max_message_attempts,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") in {"all_new_messages_failed", "systemic_failure", "run_record_failure"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
