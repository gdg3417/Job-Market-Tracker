from __future__ import annotations

import hashlib
import html
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import JobPosting, normalize_key_part, today_iso
from src.seniority import evaluate_seniority_fit

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"gh_src", "lever-source", "source", "ref", "referrer"}
REMOTE_PATTERNS = ("remote", "work from home", "wfh", "virtual")
HYBRID_PATTERNS = ("hybrid", "2 days in office", "3 days in office", "two days in office", "three days in office")
ONSITE_PATTERNS = ("on-site", "onsite", "in office", "in-office", "office-based", "5 days in office")
NO_REMOTE_PATTERNS = ("not remote", "no remote", "remote not available", "onsite only", "on-site only")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(url: Any) -> str:
    raw = clean_text(url)
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    elif re.match(r"^www\.", raw, flags=re.IGNORECASE):
        raw = "https://" + raw
    parts = urlsplit(raw)
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_PARAMS or any(key_lower.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        query_items.append((key, value))
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query_items), ""))


def parse_salary(raw_value: Any) -> tuple[int | None, int | None]:
    text = clean_text(raw_value).replace(",", "")
    if not text:
        return None, None
    numbers: list[int] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])(\$|usd)?\s*(\d+(?:\.\d+)?)\s*([kK])?(?![A-Za-z])", text):
        value = float(match.group(2))
        has_salary_marker = bool(match.group(1) or match.group(3))
        if match.group(3):
            value *= 1000
        if value >= 10000 or has_salary_marker:
            normalized = int(value)
            if normalized >= 10000:
                numbers.append(normalized)
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers), max(numbers)


def parse_currency(raw_value: Any, default: str = "USD") -> str:
    text = clean_text(raw_value).upper()
    if "CAD" in text or "C$" in text:
        return "CAD"
    if "EUR" in text or "€" in text:
        return "EUR"
    if "GBP" in text or "£" in text:
        return "GBP"
    if "USD" in text or "$" in text:
        return "USD"
    return default


def normalize_location(value: Any) -> str:
    location = clean_text(value)
    if not location:
        return ""
    replacements = {
        "United States": "US",
        "U.S.": "US",
        "USA": "US",
        "Remote -": "Remote,",
        "Remote, United States": "Remote, US",
    }
    for old, new in replacements.items():
        location = location.replace(old, new)
    return re.sub(r"\s*,\s*", ", ", location).strip(" ,")


def infer_work_model(title: str, location: str, description: str) -> tuple[str, str]:
    text = f"{title} {location} {description}".lower()
    if any(pattern in text for pattern in NO_REMOTE_PATTERNS):
        return "onsite", "in_office"
    if any(pattern in text for pattern in REMOTE_PATTERNS):
        return "remote", "remote"
    if any(pattern in text for pattern in HYBRID_PATTERNS):
        return "hybrid", "hybrid"
    if any(pattern in text for pattern in ONSITE_PATTERNS):
        return "onsite", "in_office"
    return "unknown", "unknown"


def infer_role_level(title: str) -> str:
    return evaluate_seniority_fit(title).normalized_level


def infer_role_family(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    checks = [
        ("Chief of Staff", ["chief of staff", "office of the ceo", "office of the president", "office of the gm"]),
        ("Product Line Management", ["product line", "category management", "segment management", "product strategy", "portfolio management"]),
        ("Commercial Strategy", ["commercial strategy", "revenue strategy", "pricing strategy", "go-to-market", "gtm", "sales strategy"]),
        ("Business Operations", ["business operations", "strategy operations", "bizops", "operating cadence", "business operations and strategy"]),
        ("Business Insights", ["business insights", "performance management", "commercial analytics", "business performance", "insights strategy"]),
        ("Corporate Strategy", ["corporate strategy", "value creation", "strategic planning", "enterprise strategy"]),
        ("Finance Transformation", ["finance transformation", "operating model", "business model improvement"]),
        ("FP&A", ["fp&a", "financial planning and analysis"]),
    ]
    for family, keywords in checks:
        if any(keyword in text for keyword in keywords):
            return family
    return "Unknown"


def build_job_key(company: str, title: str, location: str) -> str:
    base = "|".join([normalize_key_part(company), normalize_key_part(title), normalize_key_part(location)])
    return f"job-{hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]}"


def normalize_raw_job(raw: dict[str, Any], source_primary: str = "manual", seen_date: str | None = None) -> JobPosting:
    company = clean_text(raw.get("company") or raw.get("company_name"))
    title = clean_text(raw.get("title") or raw.get("job_title"))
    location = normalize_location(raw.get("location") or raw.get("locations"))
    description = clean_text(raw.get("description") or raw.get("description_text") or raw.get("content"))
    salary_min, salary_max = parse_salary(raw.get("salary") or raw.get("salary_range") or raw.get("compensation"))
    if raw.get("salary_min") not in (None, ""):
        parsed_min, _ = parse_salary(raw.get("salary_min"))
        salary_min = parsed_min if parsed_min is not None else int(float(str(raw["salary_min"]).replace(",", "")))
    if raw.get("salary_max") not in (None, ""):
        parsed_max, _ = parse_salary(raw.get("salary_max"))
        salary_max = parsed_max if parsed_max is not None else int(float(str(raw["salary_max"]).replace(",", "")))
    remote_status, work_model = infer_work_model(title, location, description)
    first_seen = clean_text(raw.get("first_seen_date")) or seen_date or today_iso()
    last_seen = clean_text(raw.get("last_seen_date")) or seen_date or today_iso()
    url = normalize_url(raw.get("url") or raw.get("canonical_url") or raw.get("absolute_url") or raw.get("hostedUrl"))
    return JobPosting(
        job_key=clean_text(raw.get("job_key")) or build_job_key(company, title, location),
        company=company,
        title=title,
        location=location,
        remote_status=remote_status,
        work_model=work_model,
        commute_estimate_minutes=raw.get("commute_estimate_minutes"),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=parse_currency(raw.get("salary") or raw.get("salary_range") or raw.get("compensation") or raw.get("currency")),
        total_comp_estimate=raw.get("total_comp_estimate") or salary_max,
        source_primary=clean_text(raw.get("source_primary") or source_primary),
        source_job_id=clean_text(raw.get("source_job_id") or raw.get("id") or raw.get("job_id")),
        canonical_url=url,
        description_text=description,
        first_seen_date=first_seen,
        last_seen_date=last_seen,
        missed_count=raw.get("missed_count") or 0,
        status=clean_text(raw.get("status")) or "open",
        closed_date=clean_text(raw.get("closed_date")),
        role_family=clean_text(raw.get("role_family")) or infer_role_family(title, description),
        role_level=clean_text(raw.get("role_level")) or infer_role_level(title),
        created_at=clean_text(raw.get("created_at")) or JobPosting().created_at,
        updated_at=clean_text(raw.get("updated_at")) or JobPosting().updated_at,
    )
