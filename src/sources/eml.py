from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

from src.sources.gmail_alerts import GmailAlertEmail


def read_eml(path: str | Path) -> GmailAlertEmail:
    eml_path = Path(path)
    with eml_path.open("rb") as file:
        message = BytesParser(policy=policy.default).parse(file)

    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/plain":
            text_parts.append(str(content))
        else:
            html_parts.append(str(content))

    message_id = str(message.get("X-Gmail-Message-ID") or message.get("Message-ID") or eml_path.stem)
    return GmailAlertEmail(
        message_id=message_id.strip("<>"),
        thread_id=str(message.get("X-Gmail-Thread-ID") or ""),
        subject=str(message.get("Subject") or ""),
        sender=str(message.get("From") or ""),
        received_at=str(message.get("Date") or ""),
        body_text="\n".join(text_parts),
        body_html="\n".join(html_parts),
    )
