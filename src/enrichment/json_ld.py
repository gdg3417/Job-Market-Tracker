from __future__ import annotations

import json
import re
from typing import Any, Iterable

from bs4 import BeautifulSoup


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return ", ".join(part for part in (_as_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("name", "value", "text"):
            if value.get(key) not in (None, ""):
                return _as_text(value.get(key))
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _types(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value}
    return {str(value or "").strip().lower()}


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_values(html: str) -> Iterable[Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").strip().lower()
        if script_type not in {"application/ld+json", "application/json+ld"}:
            continue
        raw = script.string if script.string is not None else script.get_text(" ", strip=True)
        raw = str(raw or "").strip()
        if not raw:
            continue
        raw = raw.removeprefix("<!--").removesuffix("-->").strip()
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def job_postings_from_html(html: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    for value in _json_values(html):
        for candidate in _walk_json(value):
            if "jobposting" in _types(candidate.get("@type")):
                postings.append(candidate)
    return postings


def _address_text(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            text = _as_text(item)
            if text:
                locations.append(text)
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else item
        parts = [
            _as_text(address.get("addressLocality")),
            _as_text(address.get("addressRegion")),
            _as_text(address.get("addressCountry")),
        ]
        location = ", ".join(part for part in parts if part)
        if not location:
            location = _as_text(item.get("name"))
        if location:
            locations.append(location)
    return " | ".join(dict.fromkeys(locations))


def _salary(posting: dict[str, Any]) -> tuple[int | None, int | None, str]:
    salary = posting.get("baseSalary")
    salary_items = salary if isinstance(salary, list) else [salary]
    for item in salary_items:
        if not isinstance(item, dict):
            continue
        currency = _as_text(item.get("currency"))
        value = item.get("value")
        if not isinstance(value, dict):
            value = item
        min_value = value.get("minValue")
        max_value = value.get("maxValue")
        exact_value = value.get("value")
        try:
            salary_min = int(float(min_value if min_value not in (None, "") else exact_value))
        except (TypeError, ValueError):
            salary_min = None
        try:
            salary_max = int(float(max_value if max_value not in (None, "") else exact_value))
        except (TypeError, ValueError):
            salary_max = None
        if salary_min is not None or salary_max is not None or currency:
            return salary_min, salary_max, currency
    return None, None, ""


def _organization_name(posting: dict[str, Any]) -> str:
    organization = posting.get("hiringOrganization")
    if isinstance(organization, list):
        organization = next((item for item in organization if isinstance(item, dict)), organization[0] if organization else "")
    return _as_text(organization)


def _description(value: Any) -> str:
    html = _as_text(value)
    if not html:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True)).strip()


def normalize_job_posting(posting: dict[str, Any]) -> dict[str, Any]:
    salary_min, salary_max, currency = _salary(posting)
    description = _description(posting.get("description"))
    location = _address_text(posting.get("jobLocation"))
    location_type = _as_text(posting.get("jobLocationType")).lower()
    applicant_location = _address_text(posting.get("applicantLocationRequirements"))
    if not location and applicant_location:
        location = applicant_location
    if "telecommute" in location_type or "remote" in location_type:
        remote_status = "remote"
        work_model = "remote"
    elif re.search(r"\bhybrid\b", description, flags=re.IGNORECASE):
        remote_status = "hybrid"
        work_model = "hybrid"
    elif location:
        remote_status = "on-site"
        work_model = "on-site"
    else:
        remote_status = "unknown"
        work_model = "unknown"

    return {
        "source_title": _as_text(posting.get("title")),
        "source_company": _organization_name(posting),
        "source_location": location,
        "description_text": description,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "currency": currency,
        "employment_type": _as_text(posting.get("employmentType")),
        "remote_status": remote_status,
        "work_model": work_model,
        "posting_date": _as_text(posting.get("datePosted"))[:10],
        "valid_through": _as_text(posting.get("validThrough"))[:10],
        "canonical_url": _as_text(posting.get("url")) or _as_text(posting.get("sameAs")),
    }


def best_job_posting(html: str) -> dict[str, Any] | None:
    candidates = [normalize_job_posting(posting) for posting in job_postings_from_html(html)]
    if not candidates:
        return None

    def score(candidate: dict[str, Any]) -> tuple[int, int]:
        populated = sum(value not in (None, "", "unknown") for value in candidate.values())
        description_length = len(str(candidate.get("description_text") or ""))
        return populated, description_length

    return max(candidates, key=score)
