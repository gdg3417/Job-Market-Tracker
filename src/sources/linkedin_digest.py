from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.normalize import clean_text

LINKEDIN_JOB_PATH_PATTERN = re.compile(r"/(?:comm/)?jobs/view/(?P<job_id>\d+)", flags=re.IGNORECASE)
DIRECT_LINK_PATTERN = re.compile(r"https?://[^\s<>'\"\)\]]+", flags=re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(
    r"\[(?P<label>.*?)\]\((?P<url>https?://[^\s\)]+)\)",
    flags=re.IGNORECASE | re.DOTALL,
)
HTML_LINK_PATTERN = re.compile(
    r"<a\b[^>]*?href=[\"'](?P<url>https?://[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)
BLOCK_END_PATTERN = re.compile(r"</(?:div|p|li|tr|td|h[1-6])>", flags=re.IGNORECASE)
BREAK_PATTERN = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
ROLE_SIGNAL_KEYWORDS = (
    "analyst",
    "analytics",
    "associate",
    "business",
    "category",
    "chief",
    "commercial",
    "consultant",
    "controller",
    "director",
    "finance",
    "financial",
    "fp&a",
    "general manager",
    "growth",
    "head",
    "insight",
    "lead",
    "manager",
    "market",
    "officer",
    "operation",
    "portfolio",
    "president",
    "pricing",
    "principal",
    "product",
    "program",
    "revenue",
    "sales",
    "senior",
    "strategy",
    "strategic",
    "transformation",
    "vice president",
)
IGNORED_CARD_LINES = {
    "actively recruiting",
    "apply",
    "apply now",
    "be an early applicant",
    "easy apply",
    "promoted",
    "save",
    "view job",
    "view jobs",
    "viewed",
}
IGNORED_CARD_LINE_PREFIXES = (
    "actively rec",
    "be among the first",
    "posted ",
    "reposted ",
)
WORK_MODEL_SUFFIX_PATTERN = re.compile(
    r"\s*\((?:on-site|onsite|hybrid|remote)\)\s*$",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class LinkedInDigestCard:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    is_rejected: bool = False
    rejection_reason: str = ""
    evidence: str = ""


@dataclass(slots=True)
class _DirectLink:
    job_id: str
    url: str
    label: str
    start: int
    end: int


def linkedin_job_id(url: str) -> str:
    try:
        parts = urlsplit(html.unescape(url or ""))
    except ValueError:
        return ""
    host = parts.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    match = LINKEDIN_JOB_PATH_PATTERN.search(parts.path)
    return match.group("job_id") if match else ""


def canonical_linkedin_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else ""


def _decoded(value: str) -> str:
    return html.unescape(value or "")


def _plain_lines(value: str) -> list[str]:
    text = _decoded(value)
    text = BREAK_PATTERN.sub("\n", text)
    text = BLOCK_END_PATTERN.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = clean_text(raw_line).strip(" []|•\t")
        if not line:
            continue
        lower = line.lower()
        if lower in IGNORED_CARD_LINES or any(lower.startswith(prefix) for prefix in IGNORED_CARD_LINE_PREFIXES):
            continue
        if lower.startswith(("http://", "https://")):
            continue
        lines.append(line)
    return lines


def _title_has_role_signal(value: str) -> bool:
    text = re.sub(r"[^a-z0-9&+]+", " ", clean_text(value).lower()).strip()
    return any(keyword in text for keyword in ROLE_SIGNAL_KEYWORDS)


def _looks_like_location(value: str) -> bool:
    text = clean_text(value)
    lower = text.lower()
    if re.search(r"\b[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\b", text):
        return True
    return any(term in lower for term in ("remote", "hybrid", "on-site", "onsite"))


def _clean_location(value: str) -> str:
    location = WORK_MODEL_SUFFIX_PATTERN.sub("", clean_text(value))
    location = re.sub(
        r"\s+(?:\d+\s+(?:minute|hour|day|week)s?\s+ago|today|new)$",
        "",
        location,
        flags=re.IGNORECASE,
    )
    return location.strip(" ·|,-")


def _split_company_location(value: str) -> tuple[str, str]:
    text = clean_text(value)
    for separator in (" · ", "•", " | "):
        if separator in text:
            company, location = text.split(separator, 1)
            return clean_text(company), _clean_location(location)
    return text, ""


def _validate_fields(title: str, company: str) -> str:
    if not title or not company:
        return "missing_title_or_company"
    if not _title_has_role_signal(title):
        return "title_lacks_role_signal"
    if _looks_like_location(title):
        return "title_looks_like_location"
    if _looks_like_location(company):
        return "company_looks_like_location"
    if clean_text(title).lower() == clean_text(company).lower():
        return "title_company_identical"
    return ""


def _parse_card_lines(lines: list[str]) -> tuple[str, str, str, str]:
    rejection_reason = "missing_title_or_company"
    for index, candidate in enumerate(lines):
        title = clean_text(candidate)
        if not _title_has_role_signal(title):
            continue
        company = ""
        location = ""
        if index + 1 < len(lines):
            company, location = _split_company_location(lines[index + 1])
        if not location and index + 2 < len(lines) and _looks_like_location(lines[index + 2]):
            location = _clean_location(lines[index + 2])
        reason = _validate_fields(title, company)
        if not reason:
            return title, company, location, ""
        rejection_reason = reason
    return "", "", "", rejection_reason


def _extract_links(value: str) -> list[_DirectLink]:
    text = _decoded(value)
    links: list[_DirectLink] = []
    covered_spans: list[tuple[int, int]] = []

    for pattern in (MARKDOWN_LINK_PATTERN, HTML_LINK_PATTERN):
        for match in pattern.finditer(text):
            url = _decoded(match.group("url")).rstrip(".,;:!?")
            job_id = linkedin_job_id(url)
            if not job_id:
                continue
            links.append(
                _DirectLink(
                    job_id=job_id,
                    url=url,
                    label=match.group("label"),
                    start=match.start(),
                    end=match.end(),
                )
            )
            covered_spans.append((match.start(), match.end()))

    for match in DIRECT_LINK_PATTERN.finditer(text):
        if any(start <= match.start() < end for start, end in covered_spans):
            continue
        url = match.group(0).rstrip(".,;:!?")
        job_id = linkedin_job_id(url)
        if not job_id:
            continue
        links.append(
            _DirectLink(
                job_id=job_id,
                url=url,
                label="",
                start=match.start(),
                end=match.end(),
            )
        )

    links.sort(key=lambda item: item.start)
    return links


def _context_lines(text: str, link: _DirectLink, previous_end: int) -> list[str]:
    start = max(previous_end, link.start - 1200)
    return _plain_lines(text[start:link.start])[-10:]


def _unique_lines(groups: list[list[str]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for line in group:
            key = clean_text(line).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(clean_text(line))
    return lines


def direct_linkedin_job_ids(value: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for link in _extract_links(value):
        if link.job_id not in seen:
            seen.add(link.job_id)
            ids.append(link.job_id)
    return ids


def is_linkedin_digest_email(
    *,
    subject: str,
    sender: str,
    body_text: str,
    body_html: str,
) -> bool:
    combined = "\n".join(part for part in (body_text, body_html) if part)
    identity = f"{subject} {sender} {combined}".lower()
    if "linkedin" not in identity and "jobalerts-noreply@linkedin.com" not in identity:
        return False

    ids: list[str] = []
    seen: set[str] = set()
    for job_id in direct_linkedin_job_ids(body_text) + direct_linkedin_job_ids(body_html):
        if job_id not in seen:
            seen.add(job_id)
            ids.append(job_id)

    # The dedicated parser is only for multi-job digests. Single-link alerts
    # must continue through the generic parser, which can use the subject line.
    return len(ids) > 1


def _parse_linkedin_digest_source(source: str) -> list[LinkedInDigestCard]:
    text = _decoded(source)
    links = _extract_links(source)
    if not links:
        return []

    order: list[str] = []
    grouped: dict[str, list[_DirectLink]] = {}
    for link in links:
        if link.job_id not in grouped:
            grouped[link.job_id] = []
            order.append(link.job_id)
        grouped[link.job_id].append(link)

    previous_direct_end = 0
    context_by_id: dict[str, list[list[str]]] = {job_id: [] for job_id in order}
    for link in links:
        context_by_id[link.job_id].append(_context_lines(text, link, previous_direct_end))
        previous_direct_end = max(previous_direct_end, link.end)

    cards: list[LinkedInDigestCard] = []
    for job_id in order:
        card_links = grouped[job_id]
        candidate_groups = [_plain_lines(link.label) for link in card_links if link.label]
        candidate_groups.extend(context_by_id[job_id])

        title = company = location = ""
        rejection_reason = "missing_title_or_company"
        evidence_lines: list[str] = []

        for group in candidate_groups:
            parsed_title, parsed_company, parsed_location, reason = _parse_card_lines(group)
            evidence_lines.extend(group)
            if parsed_title and parsed_company:
                title, company, location = parsed_title, parsed_company, parsed_location
                rejection_reason = ""
                break
            if reason:
                rejection_reason = reason

        if not title or not company:
            combined_lines = _unique_lines(candidate_groups)
            title, company, location, combined_reason = _parse_card_lines(combined_lines)
            evidence_lines.extend(combined_lines)
            if combined_reason:
                rejection_reason = combined_reason

        cards.append(
            LinkedInDigestCard(
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                url=canonical_linkedin_job_url(job_id),
                is_rejected=bool(rejection_reason),
                rejection_reason=rejection_reason,
                evidence=" | ".join(_unique_lines([evidence_lines])[:10]),
            )
        )

    return cards


def _card_quality(card: LinkedInDigestCard) -> tuple[int, int, int]:
    return (
        0 if card.is_rejected else 1,
        sum(bool(value) for value in (card.title, card.company, card.location)),
        len(card.evidence),
    )


def parse_linkedin_digest(body_text: str, body_html: str = "") -> list[LinkedInDigestCard]:
    text_cards = _parse_linkedin_digest_source(body_text) if direct_linkedin_job_ids(body_text) else []
    html_cards = _parse_linkedin_digest_source(body_html) if direct_linkedin_job_ids(body_html) else []

    if not text_cards:
        return html_cards
    if not html_cards:
        return text_cards

    order: list[str] = []
    best_by_id: dict[str, LinkedInDigestCard] = {}
    for card in text_cards + html_cards:
        if card.job_id not in best_by_id:
            order.append(card.job_id)
            best_by_id[card.job_id] = card
            continue
        if _card_quality(card) > _card_quality(best_by_id[card.job_id]):
            best_by_id[card.job_id] = card

    return [best_by_id[job_id] for job_id in order]
