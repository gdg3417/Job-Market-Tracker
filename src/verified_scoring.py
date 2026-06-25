from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from src.enrichment.fetcher import is_safe_public_url
from src.models import JobPosting

UNKNOWN_VALUES = {"", "unknown", "unspecified", "not specified", "n/a", "na", "none"}
KNOWN_ATS_DOMAIN_SUFFIXES = {
    "ashbyhq.com",
    "greenhouse.io",
    "icims.com",
    "lever.co",
    "myworkdayjobs.com",
    "oraclecloud.com",
    "phenompeople.com",
    "smartrecruiters.com",
    "successfactors.com",
    "taleo.net",
    "workday.com",
}
TRUSTED_DIRECT_SOURCE_MARKERS = {
    "ats",
    "company_site",
    "manual",
    "static",
    "website",
}
UNTRUSTED_SOURCE_MARKERS = {
    "gmail",
    "indeed",
    "linkedin",
    "manual_search",
    "search",
    "search_result",
}
MODEL_TAG_PREFIXES = (
    "authoritative_source=",
    "compensation_status=",
    "match_confidence_status=",
    "recommended_action=",
    "verification_gaps=",
)


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _hostname(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        return (urlsplit(candidate).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _host_matches(host: str, configured_host: str) -> bool:
    return bool(host and configured_host and (host == configured_host or host.endswith(f".{configured_host}")))


def _configured_authority_hosts(company_context: dict[str, Any] | None) -> set[str]:
    if not company_context:
        return set()
    hosts: set[str] = set()
    for field_name in (
        "career_domain",
        "career_search_url",
        "source_url",
        "canonical_career_url",
        "ats_url",
    ):
        host = _hostname(company_context.get(field_name))
        if host:
            hosts.add(host)
    return hosts


def is_authoritative_posting_url(url: str, company_context: dict[str, Any] | None = None) -> bool:
    if not is_safe_public_url(str(url or "")):
        return False
    host = _hostname(url)
    if not host:
        return False
    if any(_host_matches(host, suffix) for suffix in KNOWN_ATS_DOMAIN_SUFFIXES):
        return True
    return any(_host_matches(host, configured) for configured in _configured_authority_hosts(company_context))


def _meaningful_description(job: JobPosting, rules: dict[str, Any]) -> bool:
    evidence_rules = rules.get("evidence_rules", {}) or {}
    description = str(job.description_text or "")
    normalized = _normalize(description)
    prefixes = evidence_rules.get("generic_description_prefixes") or ["Extracted from Gmail job alert"]
    if any(_normalize(prefix) in normalized for prefix in prefixes if _normalize(prefix)):
        return False
    minimum_words = _safe_int(evidence_rules.get("meaningful_description_min_words"), 20)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9&'/-]*", description)) >= minimum_words


def _credible_location(job: JobPosting) -> bool:
    return any(
        _normalize(value) not in UNKNOWN_VALUES
        for value in (job.location, job.remote_status, job.work_model)
    )


def _source_marker(job: JobPosting) -> str:
    return _normalize(job.source_primary).replace(" ", "_")


def _source_is_untrusted(job: JobPosting) -> bool:
    marker = _source_marker(job)
    return any(term in marker for term in UNTRUSTED_SOURCE_MARKERS)


def _minimum_match_confidence(rules: dict[str, Any]) -> int:
    config = rules.get("verified_scoring", {}) or {}
    return _safe_int(config.get("minimum_match_confidence"), 80)


def _authoritative_source_assessment(
    job: JobPosting,
    rules: dict[str, Any],
    company_context: dict[str, Any] | None,
) -> tuple[bool, str, str]:
    minimum_confidence = _minimum_match_confidence(rules)
    confidence = job.enrichment_match_confidence
    enrichment_url = str(job.enrichment_source_url or "").strip()

    if enrichment_url or job.enrichment_status in {"partial", "enriched"}:
        candidate_url = enrichment_url or job.canonical_url
        marker = _source_marker(job)
        trusted_direct = any(term in marker for term in TRUSTED_DIRECT_SOURCE_MARKERS)
        if confidence is None:
            if trusted_direct and is_safe_public_url(candidate_url):
                return True, candidate_url, "trusted direct source"
            return False, "", f"below {minimum_confidence}"
        if confidence < minimum_confidence:
            return False, "", f"below {minimum_confidence}"
        if not is_authoritative_posting_url(candidate_url, company_context):
            return False, "", "authoritative domain not confirmed"
        return True, candidate_url, f"accepted {confidence}"

    if _source_is_untrusted(job):
        return False, "", "not validated"

    candidate_url = str(job.canonical_url or "").strip()
    marker = _source_marker(job)
    trusted_direct = any(term in marker for term in TRUSTED_DIRECT_SOURCE_MARKERS)
    if not is_authoritative_posting_url(candidate_url, company_context):
        if not trusted_direct or not is_safe_public_url(candidate_url):
            return False, "", "authoritative domain not confirmed"
    if confidence is not None and confidence < minimum_confidence:
        return False, "", f"below {minimum_confidence}"
    return True, candidate_url, "trusted direct source" if confidence is None else f"accepted {confidence}"


def verification_gaps(
    job: JobPosting,
    rules: dict[str, Any],
    company_context: dict[str, Any] | None = None,
) -> tuple[list[str], str, str]:
    gaps: list[str] = []
    if not str(job.title or "").strip():
        gaps.append("title")
    if not str(job.company or "").strip():
        gaps.append("company")
    if not _credible_location(job):
        gaps.append("location or remote designation")
    if not _meaningful_description(job, rules):
        gaps.append("meaningful description")

    authoritative, source_url, confidence_status = _authoritative_source_assessment(job, rules, company_context)
    if not authoritative:
        gaps.append("authoritative matched source")
    return gaps, source_url, confidence_status


def _recommendation(job: JobPosting) -> str:
    if job.score_status == "excluded":
        return "Do not pursue"
    if job.score_status == "verified":
        tier = str(job.verified_alert_tier or job.alert_tier or "").strip().lower()
        if tier in {"immediate_review", "strong_fit"}:
            return "Apply"
        if tier == "track_only":
            return "Review"
        return "Pass"
    if job.score_status == "partially_verified":
        return "Review recovered evidence"
    if job.potential_priority == "high":
        return "Enrich or review"
    return "Monitor"


def _replace_model_tags(explanation: str, tags: list[str]) -> str:
    parts = [part.strip() for part in str(explanation or "").split(";") if part.strip()]
    retained = [part for part in parts if not part.startswith(MODEL_TAG_PREFIXES)]
    return "; ".join([*retained, *tags])


def finalize_verified_scoring(
    job: JobPosting,
    rules: dict[str, Any],
    *,
    company_context: dict[str, Any] | None = None,
    hard_exclude: bool = False,
) -> JobPosting:
    """Apply Sprint 30 verification requirements after the raw fit score is calculated."""
    verification_rules = rules.get("verified_scoring", {}) or {}
    complete_threshold = _safe_int(verification_rules.get("complete_evidence_threshold"), 70)
    partial_threshold = _safe_int(verification_rules.get("partial_evidence_threshold"), 40)
    gaps, source_url, confidence_status = verification_gaps(job, rules, company_context)

    excluded = hard_exclude or job.alert_tier == "exclude" or "hard_exclude=true" in _normalize(job.score_explanation)
    if excluded:
        job.score_status = "excluded"
        job.verified_total_score = 0
        job.verified_alert_tier = "exclude"
    elif job.evidence_completeness_score >= complete_threshold and not gaps:
        job.score_status = "verified"
        job.verified_total_score = job.total_score
        job.verified_alert_tier = job.alert_tier
    elif job.evidence_completeness_score >= partial_threshold or job.enrichment_status in {"partial", "enriched"}:
        job.score_status = "partially_verified"
        job.verified_total_score = None
        job.verified_alert_tier = ""
    else:
        job.score_status = "provisional"
        job.verified_total_score = None
        job.verified_alert_tier = ""

    tags = [
        f"authoritative_source={source_url or 'pending'}",
        f"match_confidence_status={confidence_status}",
        f"verification_gaps={','.join(gaps) if gaps else 'none'}",
        f"recommended_action={_recommendation(job)}",
    ]
    if job.salary_min is None and job.salary_max is None and job.total_comp_estimate is None:
        tags.append("compensation_status=unknown")
    job.score_explanation = _replace_model_tags(job.score_explanation, tags)
    return job
