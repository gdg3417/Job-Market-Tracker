from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.resolution.urls import canonicalize_url


@dataclass(frozen=True, slots=True)
class AtsIdentity:
    platform: str = ""
    stable_identifier: str = ""
    requisition_id: str = ""
    authoritative: bool = False


_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("workday", ("myworkdayjobs.com", "workday.com")),
    ("greenhouse", ("greenhouse.io",)),
    ("lever", ("lever.co",)),
    ("icims", ("icims.com",)),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("successfactors", ("successfactors.com", "successfactors.eu")),
    ("oracle_recruiting", ("oraclecloud.com", "oracle.com")),
    ("jobvite", ("jobvite.com",)),
    ("phenom", ("phenompeople.com",)),
)


def _last_identifier(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment]
    for value in reversed(segments):
        clean = re.sub(r"[^A-Za-z0-9_-]+", "", value)
        if len(clean) >= 4 and clean.lower() not in {"job", "jobs", "posting", "positions", "careers", "apply"}:
            return clean
    return ""


def recognize_ats(value: Any) -> AtsIdentity:
    url = canonicalize_url(value)
    if not url:
        return AtsIdentity()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    platform = next((name for name, suffixes in _PATTERNS if any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)), "")
    query = parse_qs(parts.query)
    query_id = next(
        (
            str(query[name][0]).strip()
            for name in ("jobId", "jobid", "job", "id", "requisitionId", "reqId", "jobSeqNo", "career_job_req_id")
            if query.get(name) and str(query[name][0]).strip()
        ),
        "",
    )
    path_id = _last_identifier(parts.path)
    stable = query_id or path_id
    requisition = query_id

    if platform == "workday":
        match = re.search(r"(?:/job/[^/]+/|/details/)([^/?#]+)", parts.path, flags=re.IGNORECASE)
        slug = (match.group(1) if match else query_id or path_id).strip()
        req_match = re.search(r"(?:^|[_-])((?:R|REQ)[-_]?[A-Za-z0-9]+)$", slug, flags=re.IGNORECASE)
        requisition = req_match.group(1) if req_match else slug
        stable = requisition
    elif platform == "greenhouse":
        match = re.search(r"/jobs/(\d+)", parts.path)
        stable = match.group(1) if match else stable
        requisition = stable
    elif platform == "lever":
        match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{20,})", parts.path, flags=re.IGNORECASE)
        stable = match.group(1) if match else stable
    elif platform == "smartrecruiters":
        match = re.search(r"/job/([^/?#]+)", parts.path, flags=re.IGNORECASE)
        stable = match.group(1) if match else stable
        requisition = stable
    elif platform == "icims":
        match = re.search(r"/jobs/(\d+)", parts.path)
        stable = match.group(1) if match else query_id or stable
        requisition = stable
    elif platform == "jobvite":
        stable = query_id or stable
        requisition = stable
    elif platform == "phenom":
        match = re.search(r"/job/([^/?#]+)", parts.path, flags=re.IGNORECASE)
        stable = match.group(1) if match else stable
        requisition = stable
    elif platform == "successfactors":
        stable = query_id or stable
        requisition = stable

    return AtsIdentity(platform=platform, stable_identifier=stable, requisition_id=requisition, authoritative=bool(platform))
