from __future__ import annotations

import base64
import hashlib
import html
import re
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from src.models import JobPosting, today_iso, utc_now_iso
from src.normalize import clean_text, normalize_raw_job, normalize_url
from src.scoring import score_job

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_GMAIL_LABEL_NAME = "Job Tracker"
GENERIC_JOB_ALERT_LINES = {
    "apply",
    "apply now",
    "view job",
    "view jobs",
    "view details",
    "see job",
    "see jobs",
    "save job",
    "job alert",
    "new jobs",
    "recommended jobs",
    "jobs you may be interested in",
    "unsubscribe",
    "manage alerts",
}
LOCATION_HINTS = (
    "remote",
    "hybrid",
    "tx",
    "texas",
    "dallas",
    "plano",
    "richardson",
    "addison",
    "garland",
    "mckinney",
    "carrollton",
    "fort worth",
    "irving",
)


@dataclass(slots=True)
class GmailAlertEmail:
    message_id: str
    thread_id: str = ""
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    body_text: str = ""
    body_html: str = ""

    @property
    def combined_body(self) -> str:
        return "\n".join(part for part in [self.body_text, self.body_html] if part).strip()


@dataclass(slots=True)
class ParsedJobAlert:
    title: str
    company: str
    location: str = ""
    url: str = ""
    source: str = "gmail_alert"
    source_job_id: str = ""
    received_date: str = ""
    confidence: str = "low"
    extraction_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stable_id(*parts: str, prefix: str = "gmail") -> str:
    text = "|".join(part for part in parts if part)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _strip_html_to_lines(value: str) -> list[str]:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|li|tr|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = clean_text(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def _meaningful_lines(text: str) -> list[str]:
    lines = _strip_html_to_lines(text)
    meaningful: list[str] = []
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip(" -|•\t")
        if not normalized:
            continue
        lower = normalized.lower()
        if lower in GENERIC_JOB_ALERT_LINES:
            continue
        if lower.startswith(("http://", "https://")):
            continue
        if "unsubscribe" in lower or "privacy policy" in lower:
            continue
        meaningful.append(normalized)
    return meaningful


def extract_urls(*values: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = html.unescape(value or "")
        for match in re.finditer(r"https?://[^\s<>'\"\)\]]+", text, flags=re.IGNORECASE):
            url = normalize_url(match.group(0).rstrip(".,;:!?"))
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _detected_alert_origin(subject: str, sender: str, url: str) -> str:
    text = f"{subject} {sender} {url}".lower()
    if "linkedin" in text:
        return "linkedin"
    if "indeed" in text:
        return "indeed"
    if "google" in text and "alert" in text:
        return "google_alert"
    if "ziprecruiter" in text:
        return "ziprecruiter"
    if "workday" in text:
        return "workday_company_alert"
    if "recruit" in text or "talent" in text:
        return "recruiter_distribution"
    return "unknown_alert_source"


def _looks_like_location(line: str) -> bool:
    lower = line.lower()
    if any(hint in lower for hint in LOCATION_HINTS):
        return True
    return bool(re.search(r"\b[A-Z][a-zA-Z .'-]+,\s*[A-Z]{2}\b", line))


def _parse_single_line(line: str) -> tuple[str, str, str] | None:
    patterns = [
        r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<company>.+?)(?:\s+[-|]\s+(?P<location>.+))?$",
        r"^(?P<title>.+?)\s+[-|]\s+(?P<company>.+?)\s+[-|]\s+(?P<location>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, line, flags=re.IGNORECASE)
        if not match:
            continue
        title = clean_text(match.group("title"))
        company = clean_text(match.group("company"))
        location = clean_text(match.groupdict().get("location") or "")
        if title and company:
            return title, company, location
    return None


def _parse_from_lines(lines: list[str], subject: str = "") -> tuple[str, str, str, str]:
    for line in lines:
        parsed = _parse_single_line(line)
        if parsed:
            return (*parsed, "single_line_pattern")

    non_url_lines = [line for line in lines if not re.search(r"https?://", line, flags=re.IGNORECASE)]
    for index in range(max(0, len(non_url_lines) - 1)):
        title = clean_text(non_url_lines[index])
        company = clean_text(non_url_lines[index + 1])
        location = ""
        if index + 2 < len(non_url_lines) and _looks_like_location(non_url_lines[index + 2]):
            location = clean_text(non_url_lines[index + 2])
        if title and company and title.lower() not in GENERIC_JOB_ALERT_LINES:
            return title, company, location, "adjacent_lines"

    subject_parsed = _parse_single_line(clean_text(subject)) if subject else None
    if subject_parsed:
        return (*subject_parsed, "subject_pattern")

    return "", "", "", "review_required"


def _lines_near_url(lines: list[str], url: str) -> list[str]:
    if not url:
        return lines[:6]
    normalized_url = normalize_url(url)
    for index, line in enumerate(lines):
        if normalized_url in normalize_url(line) or url in line:
            start = max(0, index - 3)
            end = min(len(lines), index + 5)
            return lines[start:end]
    return lines[:8]


def _confidence(title: str, company: str, url: str, note: str) -> str:
    if title and company and url and note != "review_required":
        return "high"
    if (title and company) or (title and url):
        return "medium"
    return "low"


def received_date_from_header(value: str) -> str:
    if not value:
        return today_iso()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return str(value)[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", str(value)) else today_iso()
    return parsed.date().isoformat()


def parse_job_alert_email(email: GmailAlertEmail) -> list[ParsedJobAlert]:
    body = email.combined_body
    if not (email.subject or body):
        return []

    received_date = received_date_from_header(email.received_at)
    lines = _meaningful_lines(body)
    if not lines and email.subject:
        lines = _meaningful_lines(email.subject)
    urls = extract_urls(body, email.subject)
    if not urls:
        urls = [""]

    alerts: list[ParsedJobAlert] = []
    seen_keys: set[str] = set()
    for index, url in enumerate(urls):
        scoped_lines = _lines_near_url(lines, url)
        title, company, location, note = _parse_from_lines(scoped_lines, subject=email.subject)
        origin = _detected_alert_origin(email.subject, email.sender, url)
        source_job_id = _stable_id(email.message_id, url, str(index), prefix="gmail")
        confidence = _confidence(title, company, url, note)
        key = f"{title}|{company}|{location}|{url}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        alerts.append(
            ParsedJobAlert(
                title=title,
                company=company,
                location=location,
                url=url,
                source="gmail_alert",
                source_job_id=source_job_id,
                received_date=received_date,
                confidence=confidence,
                extraction_notes=f"origin={origin}; extraction={note}",
            )
        )
    return alerts


def parse_job_alert_text(email_subject: str, email_body: str) -> list[ParsedJobAlert]:
    email = GmailAlertEmail(
        message_id=_stable_id(email_subject, email_body, prefix="inline"),
        subject=email_subject,
        body_text=email_body,
        received_at=today_iso(),
    )
    return parse_job_alert_email(email)


def alert_to_raw_job(alert: ParsedJobAlert, *, seen_date: str | None = None) -> dict[str, Any]:
    current_date = seen_date or alert.received_date or today_iso()
    title = alert.title or "Review Gmail job alert"
    company = alert.company or "Unknown Company"
    description = " ".join(
        part
        for part in [
            "Extracted from Gmail job alert.",
            f"confidence={alert.confidence}.",
            alert.extraction_notes,
            "manual_review_required." if alert.confidence == "low" else "",
        ]
        if part
    )
    return {
        "company": company,
        "title": title,
        "location": alert.location,
        "url": alert.url,
        "source_primary": "gmail_alert",
        "source_job_id": alert.source_job_id,
        "description": description,
        "first_seen_date": current_date,
        "last_seen_date": current_date,
    }


def parsed_alerts_to_jobs(
    alerts: Iterable[ParsedJobAlert],
    *,
    scoring_rules: dict[str, Any] | None = None,
    seen_date: str | None = None,
) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for alert in alerts:
        job = normalize_raw_job(alert_to_raw_job(alert, seen_date=seen_date), source_primary="gmail_alert", seen_date=seen_date)
        if scoring_rules is not None:
            job = score_job(job, scoring_rules)
        jobs.append(job)
    return jobs


def parse_gmail_alert_rows(rows: Iterable[dict[str, Any]]) -> list[ParsedJobAlert]:
    alerts: list[ParsedJobAlert] = []
    for index, row in enumerate(rows):
        message_id = clean_text(row.get("message_id") or row.get("id") or f"row-{index}")
        email = GmailAlertEmail(
            message_id=message_id,
            thread_id=clean_text(row.get("thread_id")),
            subject=clean_text(row.get("subject")),
            sender=clean_text(row.get("from") or row.get("sender")),
            received_at=clean_text(row.get("received_at") or row.get("date")),
            body_text=str(row.get("body_text") or row.get("body") or ""),
            body_html=str(row.get("body_html") or ""),
        )
        alerts.extend(parse_job_alert_email(email))
    return alerts


def _load_json_file(path: str | Path) -> dict[str, Any]:
    import json

    with Path(path).expanduser().open("r", encoding="utf-8") as file:
        return json.load(file)


def build_gmail_service(client_config_path: str | Path, token_path: str | Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Gmail API dependencies are missing. Install requirements.txt after Sprint 9 updates."
        ) from exc

    token_file = Path(token_path).expanduser()
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_info(_load_json_file(token_file), [GMAIL_READONLY_SCOPE])
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(Path(client_config_path).expanduser()), [GMAIL_READONLY_SCOPE])
        creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def find_gmail_label_id(service: Any, label_name: str = DEFAULT_GMAIL_LABEL_NAME) -> str:
    response = service.users().labels().list(userId="me").execute()
    for label in response.get("labels", []):
        if label.get("name") == label_name:
            return str(label.get("id"))
    raise ValueError(f"Gmail label not found: {label_name}")


def _decode_body(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def _walk_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def gmail_message_to_email(message: dict[str, Any]) -> GmailAlertEmail:
    payload = message.get("payload") or {}
    headers = {header.get("name", "").lower(): header.get("value", "") for header in payload.get("headers", [])}
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in _walk_parts(payload):
        mime_type = str(part.get("mimeType") or "").lower()
        body_data = (part.get("body") or {}).get("data", "")
        if not body_data:
            continue
        decoded = _decode_body(body_data)
        if mime_type == "text/plain":
            text_parts.append(decoded)
        elif mime_type == "text/html":
            html_parts.append(decoded)
    return GmailAlertEmail(
        message_id=str(message.get("id") or ""),
        thread_id=str(message.get("threadId") or ""),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        received_at=headers.get("date", ""),
        body_text="\n".join(text_parts),
        body_html="\n".join(html_parts),
    )


def fetch_labeled_gmail_emails(
    service: Any,
    *,
    label_name: str = DEFAULT_GMAIL_LABEL_NAME,
    max_results: int = 50,
    query: str = "",
) -> list[GmailAlertEmail]:
    label_id = find_gmail_label_id(service, label_name)
    request = service.users().messages().list(
        userId="me",
        labelIds=[label_id],
        q=query or None,
        maxResults=max_results,
    )
    response = request.execute()
    emails: list[GmailAlertEmail] = []
    for item in response.get("messages", []) or []:
        message = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        emails.append(gmail_message_to_email(message))
    return emails


def build_gmail_run_record(
    *,
    emails_read: int,
    alerts_parsed: int,
    jobs_found: int,
    upsert_summary: dict[str, Any],
    status: str = "success",
    error_message: str = "",
    label_name: str = DEFAULT_GMAIL_LABEL_NAME,
) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    return {
        "run_id": f"sprint9_gmail_alerts_{run_timestamp}",
        "run_type": "sprint_9_gmail_alert_ingestion",
        "source_type": "gmail_alert",
        "source_name": label_name,
        "status": status,
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": jobs_found,
        "records_inserted": upsert_summary.get("jobs_created", 0),
        "records_updated": upsert_summary.get("jobs_updated", 0),
        "records_failed": 1 if error_message else 0,
        "rows_read": emails_read,
        "config_companies_rows": 0,
        "config_searches_rows": 0,
        "companies_read": 0,
        "searches_read": 0,
        "error_message": error_message,
        "notes": f"emails_read={emails_read}; alerts_parsed={alerts_parsed}; upsert_summary={upsert_summary}",
        "created_at": now,
        "updated_at": now,
    }
