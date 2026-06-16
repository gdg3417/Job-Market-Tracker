from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ParsedJobAlert:
    title: str
    company: str
    location: str = ""
    url: str = ""
    source: str = "gmail_alert"
    confidence: str = "low"


def parse_job_alert_text(email_subject: str, email_body: str) -> list[ParsedJobAlert]:
    """Placeholder parser for Sprint 9.

    Sprint 1 keeps this intentionally conservative. Gmail ingestion will need source-specific
    parsing rules for LinkedIn, Indeed, Google alerts, company alerts, and recruiter emails.
    """
    combined = f"{email_subject}\n{email_body}".strip()
    if not combined:
        return []
    return []
