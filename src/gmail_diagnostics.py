from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from src.models import utc_now_iso
from src.schema import HeaderSpec, SchemaValidationError, compare_headers
from src.sheets import with_quota_backoff
from src.sources.gmail_alerts import GMAIL_READONLY_SCOPE, GmailAlertEmail

GMAIL_FAILURES_WORKSHEET = "Gmail_Failures"
GMAIL_FAILURE_HEADERS = [
    "failure_id",
    "message_id",
    "thread_id",
    "subject",
    "sender",
    "received_at",
    "attempt_count",
    "failure_stage",
    "error_category",
    "error_message",
    "retry_eligible",
    "systemic_failure",
    "failure_fingerprint",
    "first_failed_at",
    "last_attempt_at",
    "status",
]

AUTHENTICATION_STAGES = {"authentication", "gmail_service"}
GMAIL_API_STAGES = {"list_messages", "retrieve_message"}
PARSING_STAGES = {"normalize_message", "parse_message"}
DEDUPLICATION_STAGES = {"deduplication"}
WORKBOOK_WRITE_STAGES = {
    "workbook_connect",
    "workbook_schema",
    "write_rejections",
    "write_jobs",
    "write_message_ledger",
    "write_failure_ledger",
    "record_run",
}

T = TypeVar("T")


class GmailAuthenticationError(RuntimeError):
    """Raised when Gmail credentials cannot be used without interactive authorization."""


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    stage: str
    category: str
    message: str
    retry_eligible: bool
    fingerprint: str
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _column_name(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _normalize_record(record: dict[str, Any], headers: Iterable[str]) -> dict[str, Any]:
    return {header: record.get(header, "") for header in headers}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _http_status(error: Exception) -> int | None:
    candidates = [
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(getattr(error, "response", None), "status_code", None),
        getattr(getattr(error, "resp", None), "status", None),
    ]
    for candidate in candidates:
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    match = re.search(r"\[(\d{3})\]", str(error))
    return int(match.group(1)) if match else None


def sanitize_error_message(error: Exception | str, *, limit: int = 500) -> str:
    if isinstance(error, Exception):
        text = f"{type(error).__name__}: {error}"
    else:
        text = str(error)
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(
        r'(?i)(access_token|refresh_token|client_secret|id_token)([\"\']?\s*[:=]\s*[\"\']?)[^\s,}\"]+',
        r"\1\2[redacted]",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def classify_failure(error: Exception, *, stage: str) -> FailureDiagnostic:
    status = _http_status(error)
    class_name = type(error).__name__.lower()
    message = sanitize_error_message(error)
    lower_message = message.lower()

    if (
        stage in AUTHENTICATION_STAGES
        or isinstance(error, GmailAuthenticationError)
        or class_name in {"refresherror", "defaultcredentialserror"}
        or status in {401, 403} and stage in GMAIL_API_STAGES
    ):
        category = "authentication"
        retry_eligible = False
    elif stage in GMAIL_API_STAGES:
        category = "gmail_api"
        retry_eligible = status in {408, 409, 425, 429} or bool(status and status >= 500) or isinstance(
            error,
            (ConnectionError, TimeoutError),
        )
        if status is None and not retry_eligible:
            retry_eligible = True
    elif stage in PARSING_STAGES:
        category = "parsing"
        retry_eligible = not isinstance(error, (UnicodeError, ValueError, TypeError))
    elif stage in DEDUPLICATION_STAGES:
        category = "deduplication"
        retry_eligible = True
    elif stage in WORKBOOK_WRITE_STAGES or "apierror" in class_name or "worksheet" in class_name:
        category = "workbook_write"
        retry_eligible = True
    elif stage == "configuration":
        category = "configuration"
        retry_eligible = False
    elif "duplicate" in lower_message or "dedup" in lower_message:
        category = "deduplication"
        retry_eligible = True
    else:
        category = "unknown"
        retry_eligible = True

    normalized = re.sub(r"\b\d+\b", "#", lower_message)
    fingerprint = hashlib.sha256(f"{category}|{stage}|{normalized}".encode("utf-8")).hexdigest()[:16]
    return FailureDiagnostic(
        stage=stage,
        category=category,
        message=message,
        retry_eligible=retry_eligible,
        fingerprint=fingerprint,
        http_status=status,
    )


def failure_signature(diagnostic: FailureDiagnostic) -> tuple[str, str, str]:
    return diagnostic.category, diagnostic.stage, diagnostic.fingerprint


def detect_systemic_failure(diagnostics: Iterable[FailureDiagnostic]) -> FailureDiagnostic | None:
    values = list(diagnostics)
    if len(values) < 2:
        return None
    signatures = {failure_signature(value) for value in values}
    return values[0] if len(signatures) == 1 else None


def build_noninteractive_gmail_service(client_config_path: str | Path, token_path: str | Path):
    del client_config_path
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailAuthenticationError("Gmail API dependencies are not installed") from exc

    token_file = Path(token_path).expanduser()
    if not token_file.exists():
        raise GmailAuthenticationError("Gmail token file was not found")
    try:
        token_data = json.loads(token_file.read_text(encoding="utf-8"))
        credentials = Credentials.from_authorized_user_info(token_data, [GMAIL_READONLY_SCOPE])
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_file.write_text(credentials.to_json(), encoding="utf-8")
    except (OSError, ValueError, KeyError, RefreshError) as exc:
        raise GmailAuthenticationError("Gmail token is invalid or could not be refreshed") from exc
    if not credentials.valid:
        raise GmailAuthenticationError("Gmail token is not valid; interactive authorization is disabled")
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def execute_with_bounded_retry(
    operation: Callable[[], T],
    *,
    stage: str,
    max_attempts: int = 3,
) -> T:
    attempts = max(1, int(max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            diagnostic = classify_failure(error, stage=stage)
            if not diagnostic.retry_eligible or attempt >= attempts:
                raise
    if last_error is not None:  # pragma: no cover
        raise last_error
    raise RuntimeError("Retry operation did not execute")  # pragma: no cover


def ensure_gmail_failures_worksheet(sheet_client: Any) -> None:
    worksheet = sheet_client.ensure_worksheet(
        GMAIL_FAILURES_WORKSHEET,
        rows=1000,
        cols=len(GMAIL_FAILURE_HEADERS),
    )
    current_headers = with_quota_backoff(
        lambda: worksheet.row_values(1),
        operation_name=f"read headers {GMAIL_FAILURES_WORKSHEET}",
    )
    if not any(str(value).strip() for value in current_headers):
        end_column = _column_name(len(GMAIL_FAILURE_HEADERS))
        with_quota_backoff(
            lambda: worksheet.update(
                range_name=f"A1:{end_column}1",
                values=[GMAIL_FAILURE_HEADERS],
                value_input_option="USER_ENTERED",
            ),
            operation_name=f"initialize headers {GMAIL_FAILURES_WORKSHEET}",
        )
        if hasattr(sheet_client, "_header_cache"):
            sheet_client._header_cache.pop(GMAIL_FAILURES_WORKSHEET, None)
        return
    validation = compare_headers(
        HeaderSpec(GMAIL_FAILURES_WORKSHEET, GMAIL_FAILURE_HEADERS),
        current_headers,
    )
    if not validation.ok:
        raise SchemaValidationError(
            f"Worksheet {GMAIL_FAILURES_WORKSHEET} headers do not match the expected diagnostics schema"
        )


class GmailFailureStore:
    def __init__(self, sheet_client: Any):
        self.sheet_client = sheet_client
        rows = sheet_client.read_records_with_row_numbers(GMAIL_FAILURES_WORKSHEET)
        self.records: dict[str, tuple[int, dict[str, Any]]] = {}
        for row_number, record in rows:
            failure_id = str(record.get("failure_id") or "").strip()
            if failure_id:
                self.records[failure_id] = (
                    row_number,
                    _normalize_record(record, GMAIL_FAILURE_HEADERS),
                )

    def upsert(self, record: dict[str, Any]) -> None:
        normalized = _normalize_record(record, GMAIL_FAILURE_HEADERS)
        failure_id = str(normalized.get("failure_id") or "").strip()
        if not failure_id:
            raise ValueError("Gmail failure diagnostics require failure_id")
        existing_entry = self.records.get(failure_id)
        if existing_entry is not None:
            row_number, existing = existing_entry
            normalized["first_failed_at"] = existing.get("first_failed_at") or normalized.get("first_failed_at")
            self.sheet_client.update_record(GMAIL_FAILURES_WORKSHEET, row_number, normalized)
            self.records[failure_id] = (row_number, normalized)
            return
        self.sheet_client.append_record(GMAIL_FAILURES_WORKSHEET, normalized)
        next_row = max((row for row, _ in self.records.values()), default=1) + 1
        self.records[failure_id] = (next_row, normalized)


def build_failure_record(
    email: GmailAlertEmail,
    *,
    attempt_count: int,
    diagnostic: FailureDiagnostic,
    retry_eligible: bool,
    systemic_failure: bool,
    status: str,
) -> dict[str, Any]:
    now = utc_now_iso()
    failure_id = f"{email.message_id}:{attempt_count}:{diagnostic.fingerprint}"
    return {
        "failure_id": failure_id,
        "message_id": email.message_id,
        "thread_id": email.thread_id,
        "subject": email.subject,
        "sender": email.sender,
        "received_at": email.received_at,
        "attempt_count": attempt_count,
        "failure_stage": diagnostic.stage,
        "error_category": diagnostic.category,
        "error_message": diagnostic.message,
        "retry_eligible": str(bool(retry_eligible)).lower(),
        "systemic_failure": str(bool(systemic_failure)).lower(),
        "failure_fingerprint": diagnostic.fingerprint,
        "first_failed_at": now,
        "last_attempt_at": now,
        "status": status,
    }
