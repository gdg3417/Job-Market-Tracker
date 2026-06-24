from __future__ import annotations

import re

from rapidfuzz import fuzz

from src.enrichment.fetcher import is_safe_public_url
from src.enrichment.models import EnrichmentEvidence, MatchResult
from src.models import JobPosting

LEGAL_COMPANY_TERMS = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "company",
    "co",
}
SENIORITY_TERMS = {
    "chief",
    "vice president",
    "vp",
    "director",
    "senior manager",
    "manager",
    "lead",
    "principal",
    "senior",
    "analyst",
    "associate",
    "coordinator",
}
ROLE_FAMILY_TERMS = {
    "strategy",
    "planning",
    "product",
    "pricing",
    "finance",
    "operations",
    "insights",
    "sales",
    "commercial",
    "marketing",
    "accounting",
    "technology",
    "engineering",
}


def _normalize(value: str) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"\bsr\.?\b", "senior", text)
    text = re.sub(r"\bmgr\.?\b", "manager", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_company(value: str) -> str:
    tokens = [token for token in _normalize(value).split() if token not in LEGAL_COMPANY_TERMS]
    return " ".join(tokens)


def _token_present(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def _seniority(value: str) -> str:
    text = _normalize(value)
    for term in sorted(SENIORITY_TERMS, key=len, reverse=True):
        if _token_present(text, term):
            return term
    return ""


def _role_family(value: str) -> set[str]:
    text = _normalize(value)
    return {term for term in ROLE_FAMILY_TERMS if _token_present(text, term)}


def location_similarity(left: str, right: str) -> int:
    left_normalized = _normalize(left)
    right_normalized = _normalize(right)
    if not left_normalized or not right_normalized:
        return 0
    if left_normalized == right_normalized:
        return 100
    return int(fuzz.token_set_ratio(left_normalized, right_normalized))


def locations_compatible(left: str, right: str) -> bool:
    if not str(left or "").strip() or not str(right or "").strip():
        return True
    return location_similarity(left, right) >= 70


def assess_match(job: JobPosting, evidence: EnrichmentEvidence) -> MatchResult:
    reasons: list[str] = []
    score = 0

    expected_title = _normalize(job.title)
    source_title = _normalize(evidence.source_title)
    title_similarity = int(fuzz.token_set_ratio(expected_title, source_title)) if expected_title and source_title else 0
    if title_similarity == 100:
        score += 45
        reasons.append("exact normalized title")
    elif title_similarity >= 90:
        score += 40
        reasons.append(f"strong title similarity {title_similarity}")
    elif title_similarity >= 80:
        score += 30
        reasons.append(f"moderate title similarity {title_similarity}")
    elif title_similarity >= 65:
        score += 15
        reasons.append(f"weak title similarity {title_similarity}")
    else:
        reasons.append(f"title conflict {title_similarity}")

    expected_company = _normalize_company(job.company)
    source_company = _normalize_company(evidence.source_company)
    company_similarity = int(fuzz.token_set_ratio(expected_company, source_company)) if expected_company and source_company else 0
    if company_similarity == 100:
        score += 35
        reasons.append("exact normalized company")
    elif company_similarity >= 90:
        score += 32
        reasons.append(f"strong company similarity {company_similarity}")
    elif company_similarity >= 80:
        score += 25
        reasons.append(f"moderate company similarity {company_similarity}")
    elif company_similarity:
        score -= 45
        reasons.append(f"company conflict {company_similarity}")
    else:
        reasons.append("company missing from source")

    location_match = location_similarity(job.location, evidence.source_location)
    if location_match >= 90:
        score += 10
        reasons.append("location match")
    elif location_match >= 70:
        score += 5
        reasons.append("partial location match")
    elif job.location and evidence.source_location:
        score -= 15
        reasons.append("location conflict")

    source_job_id = str(job.source_job_id or "").strip().lower()
    candidate_urls = " ".join([evidence.source_url, evidence.canonical_url]).lower()
    if source_job_id and source_job_id in candidate_urls:
        score += 10
        reasons.append("source posting id in URL")

    expected_seniority = _seniority(job.title)
    source_seniority = _seniority(evidence.source_title)
    if expected_seniority and source_seniority and expected_seniority != source_seniority:
        score -= 20
        reasons.append(f"seniority conflict {expected_seniority} vs {source_seniority}")

    expected_family = _role_family(job.title)
    source_family = _role_family(evidence.source_title)
    if expected_family and source_family and not expected_family.intersection(source_family):
        score -= 25
        reasons.append("title family conflict")

    score = max(0, min(100, score))
    outcome = "accepted" if score >= 80 else "ambiguous" if score >= 60 else "rejected"
    return MatchResult(score, outcome, tuple(reasons))


def is_authoritative_url(url: str) -> bool:
    return is_safe_public_url(url)
