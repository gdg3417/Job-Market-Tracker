from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import JobPosting

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"gh_src", "lever-source", "source", "ref", "referrer"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def normalize_key_part(value: Any) -> str:
    text = clean_text(value).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def normalize_url(url: Any) -> str:
    raw = clean_text(url)
    if not raw:
        return ""
    parts = urlsplit(raw)
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_PARAMS or any(key_lower.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        query_items.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query_items), ""))


def parse_salary(raw_value: Any) -> tuple[int | None, int | None]:
    text = clean_text(raw_value).replace(",", "")
    if not text:
        return None, None
    numbers = []
    for match in re.finditer(r"\$?\s*(\d+(?:\.\d+)?)\s*([kK])?", text):
        value = float(match.group(1))
        if match.group(2):
            value *= 1000
        if value >= 10000:
            numbers.append(int(value))
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers), max(numbers)


def infer_work_model(title: str, location: str, description: str) -> tuple[str, str]:
    text = f"{title} {location} {description}".lower()
    if "remote" in text:
        return "remote", "remote"
    if "hybrid" in text:
        return "hybrid", "hybrid"
    if "on-site" in text or "onsite" in text or "in office" in text:
        return "onsite", "in_office"
    return "unknown", "unknown"


def infer_role_level(title: str) -> str:
    text = title.lower()
    if "director" in text or "head of" in text:
        return "Director"
    if "senior manager" in text or "sr manager" in text or "sr. manager" in text:
        return "Senior Manager"
    if "manager" in text:
        return "Manager"
    if "principal" in text:
        return "Principal"
    if "lead" in text:
        return "Lead"
    if "senior" in text or "sr " in text or "sr." in text:
        return "Senior"
    return "Unknown"


def infer_role_family(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    checks = [
        ("Chief of Staff", ["chief of staff", "office of the ceo", "office of the president"]),
        ("Product Line Management", ["product line", "category management", "segment management", "product strategy"]),
        ("Commercial Strategy", ["commercial strategy", "revenue strategy", "pricing strategy", "go-to-market", "gtm"]),
        ("Business Operations", ["business operations", "strategy operations", "bizops", "operating cadence"]),
        ("Business Insights", ["business insights", "performance management", "commercial analytics", "business performance"]),
        ("Corporate Strategy", ["corporate strategy", "value creation", "strategic planning"]),
        ("Finance Transformation", ["finance transformation", "operating model", "business model improvement"]),
    ]
    for family, keywords in checks:
        if any(keyword in text for keyword in keywords):
            return family
    return "Unknown"


def build_job_key(company: str, title: str, location: str) -> str:
    base = "|".join([normalize_key_part(company), normalize_key_part(title), normalize_key_part(location)])
    return f"job-{hashlib.sha1(base.encode('utf-8')).hexdigest()[:10]}"


def normalize_raw_job(raw: dict[str, Any], source_primary: str = "manual") -> JobPosting:
    company = clean_text(raw.get("company"))
    title = clean_text(raw.get("title"))
    location = clean_text(raw.get("location"))
    description = clean_text(raw.get("description") or raw.get("description_text"))
    salary_min, salary_max = parse_salary(raw.get("salary") or raw.get("salary_range"))
    if raw.get("salary_min") not in (None, ""):
        salary_min = int(raw["salary_min"])
    if raw.get("salary_max") not in (None, ""):
        salary_max = int(raw["salary_max"])
    remote_status, work_model = infer_work_model(title, location, description)
    return JobPosting(
        job_key=raw.get("job_key") or build_job_key(company, title, location),
        company=company,
        title=title,
        location=location,
        remote_status=remote_status,
        work_model=work_model,
        salary_min=salary_min,
        salary_max=salary_max,
        total_comp_estimate=salary_max,
        source_primary=source_primary,
        source_job_id=clean_text(raw.get("source_job_id") or raw.get("id")),
        canonical_url=normalize_url(raw.get("url") or raw.get("canonical_url")),
        description_text=description,
        role_family=infer_role_family(title, description),
        role_level=infer_role_level(title),
    )
