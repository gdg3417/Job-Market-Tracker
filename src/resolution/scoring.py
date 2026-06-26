from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from src.enrichment.company_config import CompanyEnrichmentConfig, normalize_company_name
from src.models import JobPosting
from src.resolution.ats_recognition import recognize_ats
from src.resolution.models import ResolutionCandidate


@dataclass(frozen=True, slots=True)
class ResolutionThresholds:
    authoritative: int = 82
    probable: int = 70
    ambiguity_margin: int = 5
    minimum_company: int = 75
    minimum_title: int = 70


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    company_match: int
    title_match: int
    location_match: int
    requisition_match: int
    description_similarity: int
    posting_date_consistency: int
    source_domain_authority: int
    ats_identifier_consistency: int
    confidence: int
    eligible_for_authoritative: bool
    reasons: tuple[str, ...]


def _normalize(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"\bsr\.?\b", "senior", text)
    text = re.sub(r"\bmgr\.?\b", "manager", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _similarity(left: Any, right: Any) -> int:
    a, b = _normalize(left), _normalize(right)
    return int(fuzz.token_set_ratio(a, b)) if a and b else 0


def _company_similarity(job: JobPosting, candidate: ResolutionCandidate, aliases: Iterable[str]) -> int:
    expected = [job.company, *aliases]
    return max((_similarity(value, candidate.source_company) for value in expected), default=0)


def _posting_id_variants(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    variants = {re.sub(r"[^a-z0-9]+", "", text)}
    variants.update(token for token in re.findall(r"[a-z0-9]+", text) if len(token) >= 5)
    return variants - {""}


def _requisition_match(job: JobPosting, candidate: ResolutionCandidate) -> int:
    expected = _posting_id_variants(job.source_job_id)
    actual = _posting_id_variants(candidate.requisition_id or candidate.stable_identifier)
    if expected and actual and expected.intersection(actual):
        return 100
    url_text = re.sub(r"[^a-z0-9]+", "", candidate.canonical_url.lower())
    if expected and any(value in url_text for value in expected):
        return 100
    return 0


def _date_consistency(job: JobPosting, candidate: ResolutionCandidate) -> int:
    if not candidate.posting_date or not job.first_seen_date:
        return 50
    try:
        posted = date.fromisoformat(candidate.posting_date[:10])
        seen = date.fromisoformat(job.first_seen_date[:10])
    except ValueError:
        return 0
    delta = abs((seen - posted).days)
    if delta <= 7:
        return 100
    if delta <= 30:
        return 75
    if delta <= 90:
        return 40
    return 0


def _domain_authority(candidate: ResolutionCandidate, config: CompanyEnrichmentConfig | None) -> int:
    try:
        host = (urlsplit(candidate.canonical_url).hostname or "").lower()
    except ValueError:
        return 0
    identity = recognize_ats(candidate.canonical_url)
    if identity.authoritative:
        return 100
    configured = {
        str(config.career_domain or "").lower() if config else "",
        (urlsplit(config.career_search_url).hostname or "").lower() if config and config.career_search_url else "",
        (urlsplit(config.source_url).hostname or "").lower() if config and config.source_url else "",
    } - {""}
    if any(host == domain or host.endswith(f".{domain}") for domain in configured):
        return 100
    company_tokens = [token for token in normalize_company_name(candidate.source_company).split() if len(token) >= 4]
    return 70 if any(token in host.replace("-", "") for token in company_tokens) else 0


def score_candidate(
    job: JobPosting,
    candidate: ResolutionCandidate,
    *,
    config: CompanyEnrichmentConfig | None = None,
    thresholds: ResolutionThresholds | None = None,
) -> ScoreComponents:
    limits = thresholds or ResolutionThresholds()
    aliases = config.company_aliases if config else ()
    company = _company_similarity(job, candidate, aliases)
    title = _similarity(job.title, candidate.source_title)
    location = _similarity(job.location, candidate.source_location) if job.location and candidate.source_location else 50
    requisition = _requisition_match(job, candidate)
    description = _similarity(job.description_text, candidate.description_excerpt) if job.description_text and candidate.description_excerpt else 0
    posting_date = _date_consistency(job, candidate)
    domain = _domain_authority(candidate, config)
    identity = recognize_ats(candidate.canonical_url)
    configured_platform = str(config.ats_platform or "").strip().lower() if config else ""
    ats_consistency = 100 if (
        identity.platform and (not candidate.platform or candidate.platform == identity.platform)
    ) or (candidate.platform and configured_platform and candidate.platform == configured_platform and domain >= 100) else 0

    weighted = round(
        company * 0.25
        + title * 0.25
        + location * 0.10
        + requisition * 0.20
        + description * 0.08
        + posting_date * 0.04
        + domain * 0.05
        + ats_consistency * 0.03
    )
    if requisition == 100:
        weighted = max(weighted, 90 if company >= 75 else weighted)
    elif company >= 95 and title >= 95 and location >= 70 and domain >= 100:
        weighted += 15
    eligible = company >= limits.minimum_company and title >= limits.minimum_title and domain >= 70
    reasons: list[str] = []
    if company < limits.minimum_company:
        reasons.append(f"company_match_below_threshold:{company}")
    if title < limits.minimum_title:
        reasons.append(f"title_match_below_threshold:{title}")
    if domain < 70:
        reasons.append(f"source_domain_not_authoritative:{domain}")
    if requisition == 100:
        reasons.append("exact_requisition_match")
    return ScoreComponents(
        company,
        title,
        location,
        requisition,
        description,
        posting_date,
        domain,
        ats_consistency,
        max(0, min(100, weighted)),
        eligible,
        tuple(reasons),
    )


def apply_score(candidate: ResolutionCandidate, score: ScoreComponents) -> ResolutionCandidate:
    candidate.company_match = score.company_match
    candidate.title_match = score.title_match
    candidate.location_match = score.location_match
    candidate.requisition_match = score.requisition_match
    candidate.description_similarity = score.description_similarity
    candidate.posting_date_consistency = score.posting_date_consistency
    candidate.source_domain_authority = score.source_domain_authority
    candidate.ats_identifier_consistency = score.ats_identifier_consistency
    candidate.match_confidence = score.confidence
    if not score.eligible_for_authoritative:
        candidate.candidate_state = "rejected"
        candidate.rejection_reason = "; ".join(score.reasons)
    else:
        candidate.candidate_state = "scored"
    return candidate
