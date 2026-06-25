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
AUTHORITY_SUBDOMAIN_PREFIXES = ("www.", "careers.", "jobs.")
TRUSTED_DIRECT_SOURCE_MARKERS = {
    "ashby",
    "ats",
    "company_api",
    "company_site",
    "company-specific",
    "company_specific",
    "greenhouse",
    "icims",
    "lever",
    "manual",
    "oracle",
    "phenom",
    "smartrecruiters",
    "static",
    "successfactors",
    "taleo",
    "website",
    "workday",
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
    "score_status=",
    "enrichment_status=",
    "verification_gaps=",
    "verified_alert_tier=",
    "verified_score_basis=",
    "verified_total_score=",
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


def _host_variants(value: Any) -> set[str]:
    host = _hostname(value)
    if not host:
        return set()
    variants = {host}
    for prefix in AUTHORITY_SUBDOMAIN_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            variants.add(host[len(prefix) :])
    return variants


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
        hosts.update(_host_variants(company_context.get(field_name)))
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
    potential_rules = rules.get("potential_priority", {}) or {}
    evidence_rules = potential_rules.get("evidence_rules", rules.get("evidence_rules", {})) or {}
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


def _source_is_trusted_direct(job: JobPosting) -> bool:
    marker = _source_marker(job)
    return not _source_is_untrusted(job) and any(term in marker for term in TRUSTED_DIRECT_SOURCE_MARKERS)


def _minimum_match_confidence(rules: dict[str, Any]) -> int:
    config = rules.get("verified_scoring", {}) or {}
    return _safe_int(config.get("minimum_match_confidence"), 80)


def _candidate_urls(job: JobPosting) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (job.enrichment_source_url, job.canonical_url)
            if str(value or "").strip()
        )
    )


def _authoritative_source_assessment(
    job: JobPosting,
    rules: dict[str, Any],
    company_context: dict[str, Any] | None,
) -> tuple[bool, str, str]:
    minimum_confidence = _minimum_match_confidence(rules)
    confidence = job.enrichment_match_confidence
    candidate_urls = _candidate_urls(job)
    untrusted_source = _source_is_untrusted(job)
    trusted_direct = _source_is_trusted_direct(job)

    if confidence is not None and confidence < minimum_confidence:
        return False, "", f"below {minimum_confidence}"
    if confidence is None and (untrusted_source or not trusted_direct):
        return False, "", "not validated"

    authoritative_url = next(
        (url for url in candidate_urls if is_authoritative_posting_url(url, company_context)),
        "",
    )
    if authoritative_url:
        confidence_status = f"accepted {confidence}" if confidence is not None else "trusted authoritative source"
        return True, authoritative_url, confidence_status

    safe_url = next((url for url in candidate_urls if is_safe_public_url(url)), "")
    if trusted_direct and safe_url:
        confidence_status = f"accepted {confidence}" if confidence is not None else "trusted direct source"
        return True, safe_url, confidence_status

    if confidence is None and (job.enrichment_source_url or job.enrichment_status in {"partial", "enriched"}):
        return False, "", f"below {minimum_confidence}"
    return False, "", "authoritative domain not confirmed"


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


def _compensation_unknown(job: JobPosting) -> bool:
    return job.salary_min is None and job.salary_max is None and job.total_comp_estimate is None


def _tier_for_score(score: int, rules: dict[str, Any]) -> str:
    thresholds = rules.get("alert_thresholds", {}) or {}
    if score >= _safe_int(thresholds.get("immediate_review"), 85):
        return "immediate_review"
    if score >= _safe_int(thresholds.get("strong_fit"), 75):
        return "strong_fit"
    if score >= _safe_int(thresholds.get("track_only"), 65):
        return "track_only"
    return str((rules.get("alert_tiers", {}) or {}).get("below_track", "ignore"))


def _verified_score(job: JobPosting, rules: dict[str, Any]) -> tuple[int, str, str]:
    if not _compensation_unknown(job):
        return job.total_score, job.alert_tier, "complete_category_scale"

    weights = rules.get("category_weights", {}) or {}
    score_scale = _safe_int(rules.get("score_scale"), 100)
    configured_total = sum(max(0, _safe_int(value)) for value in weights.values()) or score_scale
    compensation_weight = max(0, _safe_int(weights.get("comp_score"), 0))
    available_total = max(1, configured_total - compensation_weight)
    normalized_score = min(score_scale, round(job.total_score * score_scale / available_total))
    return normalized_score, _tier_for_score(normalized_score, rules), "normalized_without_compensation"


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
    score_basis = "pending_verification"

    excluded = hard_exclude or job.alert_tier == "exclude" or "hard_exclude=true" in _normalize(job.score_explanation)
    if excluded:
        job.score_status = "excluded"
        job.verified_total_score = 0
        job.verified_alert_tier = "exclude"
        score_basis = "hard_exclusion"
    elif job.evidence_completeness_score >= complete_threshold and not gaps:
        job.score_status = "verified"
        verified_score, verified_tier, score_basis = _verified_score(job, rules)
        job.verified_total_score = verified_score
        job.verified_alert_tier = verified_tier
    elif job.evidence_completeness_score >= partial_threshold or job.enrichment_status in {"partial", "enriched"}:
        job.score_status = "partially_verified"
        job.verified_total_score = None
        job.verified_alert_tier = ""
    else:
        job.score_status = "provisional"
        job.verified_total_score = None
        job.verified_alert_tier = ""

    if job.score_status in {"provisional", "partially_verified"} and job.potential_priority == "high":
        if job.enrichment_status in {"", "not_required", "closed"}:
            job.enrichment_status = "pending"
            job.enrichment_priority = "high"
    elif job.score_status in {"verified", "excluded"} and job.enrichment_status not in {"partial", "enriched"}:
        job.enrichment_status = "not_required"
        job.enrichment_priority = ""

    tags = [
        f"score_status={job.score_status}",
        f"enrichment_status={job.enrichment_status}",
        f"authoritative_source={source_url or 'pending'}",
        f"match_confidence_status={confidence_status}",
        f"verification_gaps={','.join(gaps) if gaps else 'none'}",
        f"verified_total_score={job.verified_total_score if job.verified_total_score is not None else 'pending'}",
        f"verified_alert_tier={job.verified_alert_tier or 'pending'}",
        f"verified_score_basis={score_basis}",
        f"recommended_action={_recommendation(job)}",
    ]
    if _compensation_unknown(job):
        tags.append("compensation_status=unknown")
    job.score_explanation = _replace_model_tags(job.score_explanation, tags)
    return job
