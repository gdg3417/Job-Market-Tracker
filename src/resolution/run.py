from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from src.dedupe import SOURCE_FIELDS
from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult, discover_ats_candidates
from src.enrichment.company_config import CompanyEnrichmentConfig, load_company_configs, resolve_company_config
from src.enrichment.extractors import extract_job_evidence
from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError, FetchPolicy
from src.enrichment.merge import merge_verified_evidence
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem
from src.enrichment.queue import evidence_id_for
from src.enrichment.search import (
    DisabledSearchProvider,
    DuckDuckGoHtmlSearchProvider,
    SearchProvider,
    build_search_plan,
    is_authoritative_candidate,
    is_denied_automatic_candidate,
)
from src.models import JobPosting
from src.resolution.ats_recognition import recognize_ats
from src.resolution.config import ResolutionSettings
from src.resolution.models import (
    MANUAL_DECISIONS,
    PostingResolution,
    ResolutionCandidate,
    candidate_id_for,
    resolution_id_for,
    utc_now_iso,
)
from src.resolution.scoring import apply_score, score_candidate
from src.resolution.urls import canonicalize_url

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "posting_resolution.yml"
DEFAULT_PRIORITY_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "potential_priority_rules.yml"
OPEN_STATUSES = {"open", "reopened"}
HIGH_SIGNAL_TITLE_TERMS = (
    "director",
    "senior manager",
    "sr manager",
    "national manager",
    "strategy",
    "operations",
    "product",
    "category",
    "commercial",
    "revenue",
)


@dataclass(slots=True)
class ResolutionRunSummary:
    jobs_evaluated: int = 0
    resolution_attempts: int = 0
    resolution_succeeded: int = 0
    resolved_authoritative: int = 0
    resolved_probable: int = 0
    ambiguous: int = 0
    not_found: int = 0
    blocked: int = 0
    unsupported: int = 0
    retryable_failures: int = 0
    manual_overrides: int = 0
    candidates_discovered: int = 0
    candidate_rows_written: int = 0
    resolution_rows_written: int = 0
    evidence_written: int = 0
    source_rows_written: int = 0
    jobs_updated: int = 0
    manual_intervention_required: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _records(sheet_client: Any, worksheet: str) -> list[tuple[int, dict[str, Any]]]:
    try:
        if hasattr(sheet_client, "read_records_with_row_numbers"):
            return list(sheet_client.read_records_with_row_numbers(worksheet))
        return [(index + 2, row) for index, row in enumerate(sheet_client.read_records(worksheet))]
    except Exception as exc:
        if exc.__class__.__name__ in {"WorksheetNotFound", "KeyError"}:
            return []
        raise


def _jobs(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [(row, JobPosting.from_dict(record)) for row, record in _records(sheet_client, "Jobs")]


def _update_job(sheet_client: Any, row_number: int, job: JobPosting) -> None:
    if hasattr(sheet_client, "update_job"):
        sheet_client.update_job(row_number, job)
    else:
        sheet_client.update_record("Jobs", row_number, job.to_dict())


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "accepted"}



def _match_text(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"\bsr\.?\b", "senior", text)
    text = re.sub(r"\bmgr\.?\b", "manager", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _metadata_similarity(left: Any, right: Any) -> int:
    a, b = _match_text(left), _match_text(right)
    return int(fuzz.token_set_ratio(a, b)) if a and b else 0


def _source_row_matches_job(job: JobPosting, row: dict[str, Any]) -> bool:
    source_company = str(row.get("company") or "").strip()
    source_title = str(row.get("title") or "").strip()
    if source_company and _metadata_similarity(job.company, source_company) < 75:
        return False
    if source_title and _metadata_similarity(job.title, source_title) < 70:
        return False
    return True


def _priority(job: JobPosting, *, target: bool, has_partial_evidence: bool) -> tuple[int, int, str]:
    title = job.title.lower()
    if job.potential_priority == "high":
        rank = 0
    elif target:
        rank = 1
    elif any(term in title for term in HIGH_SIGNAL_TITLE_TERMS):
        rank = 2
    elif has_partial_evidence or job.score_status == "partially_verified":
        rank = 3
    else:
        rank = 4
    return rank, -int(job.potential_priority_score or 0), job.first_seen_date or ""


def _company_target(job: JobPosting, target_rows: list[dict[str, Any]]) -> bool:
    expected = " ".join(str(job.company or "").lower().split())
    if not expected:
        return False
    for row in target_rows:
        if not _truthy(row.get("active", True)):
            continue
        names = [row.get("company_name"), row.get("parent_company")]
        for name in names:
            normalized = " ".join(str(name or "").lower().split())
            if normalized and (expected == normalized or expected in normalized or normalized in expected):
                return True
    return False


def _candidate_from_evidence(
    evidence: EnrichmentEvidence,
    *,
    job_key: str,
    discovery_order: int,
    discovery_method: str,
    observed_url: str = "",
) -> ResolutionCandidate:
    canonical = canonicalize_url(evidence.canonical_url or evidence.source_url)
    identity = recognize_ats(canonical)
    return ResolutionCandidate(
        candidate_id=candidate_id_for(job_key, canonical, discovery_method),
        job_key=job_key,
        discovery_order=discovery_order,
        discovery_method=discovery_method,
        source_type=evidence.source_type,
        observed_url=observed_url or evidence.source_url,
        canonical_url=canonical,
        platform=identity.platform,
        stable_identifier=identity.stable_identifier,
        requisition_id=identity.requisition_id,
        source_title=evidence.source_title,
        source_company=evidence.source_company,
        source_location=evidence.source_location,
        posting_date=evidence.posting_date,
        description_excerpt=evidence.description_text[:4000],
    )



def _candidate_from_ats(
    candidate: AtsCandidate,
    *,
    job_key: str,
    discovery_order: int,
    discovery_method: str,
) -> ResolutionCandidate:
    canonical = canonicalize_url(candidate.url)
    identity = recognize_ats(canonical)
    stable_identifier = candidate.posting_id or identity.stable_identifier
    return ResolutionCandidate(
        candidate_id=candidate_id_for(job_key, canonical, discovery_method),
        job_key=job_key,
        discovery_order=discovery_order,
        discovery_method=discovery_method,
        source_type=f"{candidate.platform}_resolution",
        observed_url=candidate.url,
        canonical_url=canonical,
        platform=candidate.platform or identity.platform,
        stable_identifier=stable_identifier,
        requisition_id=candidate.posting_id or identity.requisition_id,
        source_title=candidate.title,
        source_company=candidate.company,
        source_location=candidate.location,
        posting_date=candidate.posting_date,
        description_excerpt=candidate.description_text[:4000],
    )

def _evidence_from_ats(candidate: AtsCandidate, *, job_key: str, enrichment_id: str, now: str) -> EnrichmentEvidence:
    source_url = canonicalize_url(candidate.url)
    content_hash = hashlib.sha256(json.dumps(asdict(candidate), sort_keys=True, default=str).encode("utf-8")).hexdigest()
    evidence = EnrichmentEvidence(
        job_key=job_key,
        enrichment_id=enrichment_id,
        source_type=f"{candidate.platform}_resolution",
        source_url=source_url,
        retrieved_at=now,
        http_status=200,
        canonical_url=source_url,
        source_title=candidate.title,
        source_company=candidate.company,
        source_location=candidate.location,
        description_text=candidate.description_text,
        salary_min=candidate.salary_min,
        salary_max=candidate.salary_max,
        currency=candidate.currency,
        employment_type=candidate.employment_type,
        remote_status=candidate.remote_status,
        work_model=candidate.work_model,
        posting_date=candidate.posting_date,
        valid_through=candidate.valid_through,
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(enrichment_id, source_url, content_hash)
    return evidence


def _fetch_candidate(
    url: str,
    *,
    observed_url: str,
    job_key: str,
    enrichment_id: str,
    discovery_order: int,
    discovery_method: str,
    now: str,
    fetcher: DirectLinkFetcher | Any,
) -> tuple[ResolutionCandidate | None, EnrichmentEvidence | None, str, str, bool]:
    canonical_input = canonicalize_url(url)
    if not canonical_input:
        return None, None, "unsupported", "URL is not a safe public HTTP or HTTPS destination", False
    try:
        fetched = fetcher.fetch(canonical_input)
    except EnrichmentFetchError as exc:
        state = "retryable_failure" if exc.retryable else "blocked" if exc.error_type in {"access_blocked", "unsafe_url"} else "not_found"
        return None, None, state, f"{exc.error_type}: {exc}", exc.retryable
    evidence = extract_job_evidence(fetched, job_key=job_key, enrichment_id=enrichment_id, retrieved_at=now)
    if evidence is None:
        return None, None, "unsupported", "Resolved page did not contain a recognizable job posting", False
    evidence.source_url = canonicalize_url(evidence.source_url or fetched.final_url)
    evidence.canonical_url = canonicalize_url(evidence.canonical_url or fetched.final_url)
    identity = recognize_ats(evidence.canonical_url)
    candidate = _candidate_from_evidence(
        evidence,
        job_key=job_key,
        discovery_order=discovery_order,
        discovery_method=discovery_method,
        observed_url=observed_url,
    )
    candidate.platform = identity.platform
    candidate.stable_identifier = identity.stable_identifier
    candidate.requisition_id = identity.requisition_id
    return candidate, evidence, "resolved", "", False



def _career_search_links(html: str, base_url: str, *, limit: int) -> list[str]:
    if not html or limit <= 0:
        return []
    soup = BeautifulSoup(html, "html.parser")
    selected: list[str] = []
    seen: set[str] = set()
    markers = ("/job/", "/jobs/", "jobid=", "job_id=", "requisition", "/position/")
    for anchor in soup.find_all("a", href=True):
        url = canonicalize_url(urljoin(base_url, str(anchor.get("href") or "")))
        text = f"{url} {anchor.get_text(' ', strip=True)}".lower()
        if not url or url in seen or not any(marker in text for marker in markers):
            continue
        selected.append(url)
        seen.add(url)
        if len(selected) >= limit:
            break
    return selected

def _dedupe_candidates(candidates: Iterable[ResolutionCandidate]) -> list[ResolutionCandidate]:
    selected: dict[str, ResolutionCandidate] = {}

    def preference(candidate: ResolutionCandidate) -> tuple[int, int, int, int]:
        return (
            1 if candidate.candidate_state == "manual_override" else 0,
            candidate.match_confidence,
            -candidate.discovery_order,
            len(candidate.description_excerpt),
        )

    for candidate in candidates:
        key = canonicalize_url(candidate.canonical_url)
        if not key:
            continue
        candidate.canonical_url = key
        current = selected.get(key)
        if current is None:
            selected[key] = candidate
            continue

        winner, other = (candidate, current) if preference(candidate) > preference(current) else (current, candidate)
        methods = []
        for value in (current.discovery_method, candidate.discovery_method):
            for method in str(value or "").split("|"):
                clean = method.strip()
                if clean and clean not in methods:
                    methods.append(clean)
        source_types = []
        for value in (current.source_type, candidate.source_type):
            for source_type in str(value or "").split("|"):
                clean = source_type.strip()
                if clean and clean not in source_types:
                    source_types.append(clean)
        winner.discovery_method = "|".join(methods)
        winner.source_type = "|".join(source_types)
        winner.discovery_order = min(current.discovery_order, candidate.discovery_order)
        winner.discovered_at = min(
            value for value in (current.discovered_at, candidate.discovered_at) if value
        ) if current.discovered_at or candidate.discovered_at else ""
        selected[key] = winner
    return sorted(
        selected.values(),
        key=lambda item: (0 if item.candidate_state == "manual_override" else 1, item.discovery_order, item.canonical_url),
    )


def _upsert_record(
    sheet_client: Any,
    worksheet: str,
    key_name: str,
    record: dict[str, Any],
    existing: dict[str, tuple[int, dict[str, Any]]],
) -> bool:
    key = str(record.get(key_name) or "")
    current = existing.get(key)
    if current is None:
        sheet_client.append_record(worksheet, record)
        row_number = max((row for row, _ in existing.values()), default=1) + 1
        existing[key] = (row_number, dict(record))
        return True
    row_number, prior = current
    record = dict(record)
    immutable_fields = {
        "Posting_Resolution": ("created_at",),
        "Resolution_Candidates": ("discovered_at",),
        "Job_Sources": ("first_seen_date", "created_at"),
        "Enrichment_Evidence": ("created_at",),
    }.get(worksheet, ())
    for field_name in immutable_fields:
        if prior.get(field_name) not in (None, ""):
            record[field_name] = prior[field_name]
    if prior == record:
        return False
    sheet_client.update_record(worksheet, row_number, record)
    existing[key] = (row_number, dict(record))
    return True


def _source_record(job: JobPosting, candidate: ResolutionCandidate, now: str) -> dict[str, Any]:
    material = f"authoritative_resolution|{job.job_key}|{candidate.canonical_url}"
    source_key = f"src-{hashlib.sha1(material.encode('utf-8')).hexdigest()[:16]}"
    date_value = now[:10]
    record = {
        "source_key": source_key,
        "job_key": job.job_key,
        "company": job.company,
        "title": job.title,
        "source_primary": job.source_primary,
        "source_type": "authoritative_resolution",
        "source_job_id": candidate.requisition_id or candidate.stable_identifier,
        "canonical_url": candidate.canonical_url,
        "source_url": candidate.observed_url or candidate.canonical_url,
        "first_seen_date": date_value,
        "last_seen_date": date_value,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    return {name: record.get(name, "") for name in SOURCE_FIELDS}


def _resolution_from_selection(
    prior: PostingResolution,
    *,
    state: str,
    candidate: ResolutionCandidate | None,
    candidate_count: int,
    attempted_at: str,
    blocker_reason: str = "",
    error_message: str = "",
    latency_seconds: int = 0,
) -> PostingResolution:
    selected = candidate or ResolutionCandidate()
    return PostingResolution(
        resolution_id=prior.resolution_id,
        job_key=prior.job_key,
        resolution_state=state,
        authoritative_url=selected.canonical_url if state in {"resolved_authoritative", "manual_override"} else "",
        platform=selected.platform,
        stable_identifier=selected.stable_identifier,
        candidate_count=candidate_count,
        match_confidence=selected.match_confidence if candidate else None,
        company_match=selected.company_match,
        title_match=selected.title_match,
        location_match=selected.location_match,
        requisition_match=selected.requisition_match,
        description_similarity=selected.description_similarity,
        posting_date_consistency=selected.posting_date_consistency,
        source_domain_authority=selected.source_domain_authority,
        ats_identifier_consistency=selected.ats_identifier_consistency,
        attempted_at=attempted_at,
        resolved_at=attempted_at if state in {"resolved_authoritative", "manual_override"} else "",
        resolution_latency_seconds=max(0, int(latency_seconds)),
        blocker_reason=blocker_reason,
        error_message=error_message[:1000],
        manual_authoritative_url=prior.manual_authoritative_url,
        manual_resolution_decision=prior.manual_resolution_decision,
        manual_reviewer=prior.manual_reviewer,
        manual_review_date=prior.manual_review_date,
        manual_notes=prior.manual_notes,
        created_at=prior.created_at or attempted_at,
        updated_at=attempted_at,
    )


def _select_state(
    candidates: list[ResolutionCandidate],
    settings: ResolutionSettings,
) -> tuple[str, ResolutionCandidate | None, str]:
    ranked = sorted(candidates, key=lambda item: (item.match_confidence, item.requisition_match, len(item.description_excerpt)), reverse=True)
    eligible = [item for item in ranked if item.candidate_state != "rejected"]
    if not eligible:
        return "not_found", ranked[0] if ranked else None, "No candidate passed company, title, and authority gates"
    best = eligible[0]
    close = [item for item in eligible[1:] if best.match_confidence - item.match_confidence <= settings.thresholds.ambiguity_margin]
    if close and best.match_confidence >= settings.thresholds.probable:
        return "ambiguous", best, "Multiple candidates are within the configured ambiguity margin"
    if best.match_confidence >= settings.thresholds.authoritative:
        return "resolved_authoritative", best, ""
    if best.match_confidence >= settings.thresholds.probable:
        return "resolved_probable", best, "Candidate is plausible but below the authoritative threshold"
    return "not_found", best, "Best candidate is below the probable threshold"


def run_posting_resolution(
    sheet_client: Any,
    *,
    limit: int = 10,
    job_key: str = "",
    now: str | None = None,
    settings: ResolutionSettings | None = None,
    configs: Iterable[CompanyEnrichmentConfig] | None = None,
    fetcher: DirectLinkFetcher | Any | None = None,
    search_provider: SearchProvider | None = None,
    ats_discovery: Callable[..., AtsDiscoveryResult] = discover_ats_candidates,
    priority_rules: dict[str, Any] | None = None,
) -> ResolutionRunSummary:
    timestamp = now or utc_now_iso()
    options = settings or ResolutionSettings.from_yaml(DEFAULT_CONFIG_PATH)
    if priority_rules is None:
        from src.potential_priority import load_potential_priority_rules

        priority_rules = load_potential_priority_rules(DEFAULT_PRIORITY_RULES_PATH)
    company_configs = list(configs) if configs is not None else load_company_configs(sheet_client)
    fetch_client = fetcher or DirectLinkFetcher(policy=FetchPolicy(timeout_seconds=options.timeout_seconds))
    provider = search_provider or DisabledSearchProvider()

    job_rows = [(row, job) for row, job in _jobs(sheet_client) if not job_key or job.job_key == job_key]
    source_rows = _records(sheet_client, "Job_Sources")
    queue_rows = _records(sheet_client, "Enrichment_Queue")
    evidence_rows = _records(sheet_client, "Enrichment_Evidence")
    target_rows = [record for _, record in _records(sheet_client, "Target_Companies")]
    resolution_rows = _records(sheet_client, "Posting_Resolution")
    candidate_rows = _records(sheet_client, "Resolution_Candidates")

    sources_by_job: dict[str, list[dict[str, Any]]] = {}
    for _, row in source_rows:
        sources_by_job.setdefault(str(row.get("job_key") or ""), []).append(row)
    evidence_by_job: dict[str, list[dict[str, Any]]] = {}
    for _, row in evidence_rows:
        evidence_by_job.setdefault(str(row.get("job_key") or ""), []).append(row)
    queue_by_job = {
        item.job_key: (row_number, item)
        for row_number, raw in queue_rows
        if (item := EnrichmentQueueItem.from_dict(raw)).job_key
    }
    resolutions = {
        str(row.get("job_key") or ""): (row_number, PostingResolution.from_dict(row))
        for row_number, row in resolution_rows
        if str(row.get("job_key") or "")
    }
    resolution_index = {
        resolution.resolution_id: (row, resolution.to_dict())
        for row, resolution in resolutions.values()
    }
    candidate_index = {
        str(row.get("candidate_id") or ""): (row_number, row)
        for row_number, row in candidate_rows
        if str(row.get("candidate_id") or "")
    }
    evidence_index = {
        str(row.get("evidence_id") or ""): (row_number, row)
        for row_number, row in evidence_rows
        if str(row.get("evidence_id") or "")
    }
    source_index = {
        str(row.get("source_key") or ""): (row_number, row)
        for row_number, row in source_rows
        if str(row.get("source_key") or "")
    }

    eligible: list[tuple[tuple[int, int, str], int, JobPosting]] = []
    for row_number, job in job_rows:
        prior_resolution = resolutions.get(job.job_key, (0, PostingResolution(job_key=job.job_key)))[1]
        has_manual_action = bool(prior_resolution.manual_resolution_decision)
        if job.status not in OPEN_STATUSES or job.score_status == "excluded" or job.potential_priority == "excluded":
            continue
        if job.score_status == "verified" and not has_manual_action:
            continue
        prior_evidence = any(_truthy(row.get("accepted")) for row in evidence_by_job.get(job.job_key, []))
        eligible.append((_priority(job, target=_company_target(job, target_rows), has_partial_evidence=prior_evidence), row_number, job))
    eligible.sort(key=lambda item: item[0])

    summary = ResolutionRunSummary()
    for _sort_key, job_row, job in eligible[: max(0, limit)]:
        job_started = perf_counter()
        summary.jobs_evaluated += 1
        summary.resolution_attempts += 1
        prior = resolutions.get(job.job_key, (0, PostingResolution(job_key=job.job_key, created_at=timestamp)))[1]
        prior.job_key = job.job_key
        prior.resolution_id = prior.resolution_id or resolution_id_for(job.job_key)
        config = resolve_company_config(job.company, company_configs)
        enrichment_id = queue_by_job.get(job.job_key, (0, EnrichmentQueueItem(enrichment_id=f"resolution-{job.job_key}")))[1].enrichment_id
        enrichment_id = enrichment_id or f"resolution-{job.job_key}"
        discovered: list[ResolutionCandidate] = []
        candidate_evidence: dict[str, EnrichmentEvidence] = {}
        failure_states: list[tuple[str, str]] = []
        seen_urls: set[str] = set()

        def add_evidence_candidate(evidence: EnrichmentEvidence, order: int, method: str, observed_url: str = "") -> None:
            candidate = _candidate_from_evidence(
                evidence,
                job_key=job.job_key,
                discovery_order=order,
                discovery_method=method,
                observed_url=observed_url,
            )
            if not candidate.canonical_url or candidate.canonical_url in seen_urls:
                return
            seen_urls.add(candidate.canonical_url)
            candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
            discovered.append(candidate)
            candidate_evidence[candidate.candidate_id] = evidence

        for raw in evidence_by_job.get(job.job_key, []):
            evidence = EnrichmentEvidence.from_dict(raw)
            if _truthy(evidence.accepted) and (evidence.canonical_url or evidence.source_url):
                add_evidence_candidate(evidence, 1, "existing_authoritative_evidence")

        job_sources = [row for row in sources_by_job.get(job.job_key, []) if _source_row_matches_job(job, row)]
        direct_urls: list[tuple[str, str]] = []
        queue_item = queue_by_job.get(job.job_key, (0, None))[1]
        for observed in [
            job.canonical_url,
            queue_item.matched_url if queue_item else "",
            queue_item.lead_url if queue_item else "",
            *[row.get("canonical_url") or row.get("source_url") for row in job_sources],
        ]:
            canonical = canonicalize_url(observed)
            if canonical and canonical not in {value for _, value in direct_urls}:
                direct_urls.append((str(observed or ""), canonical))
        direct_attempts = 0
        for observed, url in direct_urls:
            if url in seen_urls or is_denied_automatic_candidate(url):
                continue
            if direct_attempts >= options.direct_url_budget:
                break
            direct_attempts += 1
            candidate, evidence, state, message, _retryable = _fetch_candidate(
                url,
                observed_url=observed,
                job_key=job.job_key,
                enrichment_id=enrichment_id,
                discovery_order=2,
                discovery_method="direct_url_resolution",
                now=timestamp,
                fetcher=fetch_client,
            )
            if candidate and evidence:
                candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                seen_urls.add(candidate.canonical_url)
                discovered.append(candidate)
                candidate_evidence[candidate.candidate_id] = evidence
            elif message:
                failure_states.append((state, message))

        if config is not None and config.career_search_url and options.career_search_link_budget > 0:
            search_url = canonicalize_url(config.career_search_url)
            if search_url:
                try:
                    search_page = fetch_client.fetch(search_url)
                    search_evidence = extract_job_evidence(
                        search_page,
                        job_key=job.job_key,
                        enrichment_id=enrichment_id,
                        retrieved_at=timestamp,
                    )
                    if search_evidence is not None:
                        add_evidence_candidate(search_evidence, 3, "configured_employer_career_search", search_url)
                    link_budget = options.career_search_link_budget
                    for candidate_url in _career_search_links(search_page.text, search_page.final_url, limit=link_budget):
                        if candidate_url in seen_urls or not is_authoritative_candidate(candidate_url, config, company=job.company):
                            continue
                        candidate, evidence, state, message, _retryable = _fetch_candidate(
                            candidate_url,
                            observed_url=candidate_url,
                            job_key=job.job_key,
                            enrichment_id=enrichment_id,
                            discovery_order=3,
                            discovery_method="configured_employer_career_search",
                            now=timestamp,
                            fetcher=fetch_client,
                        )
                        if candidate and evidence:
                            candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                            seen_urls.add(candidate.canonical_url)
                            discovered.append(candidate)
                            candidate_evidence[candidate.candidate_id] = evidence
                        elif message:
                            failure_states.append((state, message))
                except EnrichmentFetchError as exc:
                    failure_states.append((
                        "retryable_failure" if exc.retryable else "blocked" if exc.error_type == "access_blocked" else "not_found",
                        f"configured career search: {exc.error_type}: {exc}",
                    ))

        if config is not None:
            try:
                ats_result = ats_discovery(
                    config,
                    expected_title=job.title,
                    expected_location=job.location,
                    session=getattr(fetch_client, "session", None),
                    timeout_seconds=options.timeout_seconds,
                )
            except Exception as exc:
                ats_result = AtsDiscoveryResult(config.ats_platform or "unknown", "failed", error_message=str(exc))
            if ats_result.status in {"failed", "invalid_config"}:
                failure_states.append(("retryable_failure" if ats_result.status == "failed" else "unsupported", ats_result.error_message))
            elif ats_result.status == "configured_only":
                failure_states.append(("unsupported", ats_result.error_message))
            for ats_candidate in ats_result.candidates[: options.ats_candidate_budget]:
                evidence = _evidence_from_ats(ats_candidate, job_key=job.job_key, enrichment_id=enrichment_id, now=timestamp)
                candidate = _candidate_from_ats(
                    ats_candidate,
                    job_key=job.job_key,
                    discovery_order=4,
                    discovery_method="configured_ats_board",
                )
                if not candidate.canonical_url or candidate.canonical_url in seen_urls:
                    continue
                candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                seen_urls.add(candidate.canonical_url)
                discovered.append(candidate)
                candidate_evidence[candidate.candidate_id] = evidence

        for row in job_sources:
            url = canonicalize_url(row.get("canonical_url") or row.get("source_url"))
            identity = recognize_ats(url)
            if not url or not identity.platform or url in seen_urls:
                continue
            candidate, evidence, state, message, _retryable = _fetch_candidate(
                url,
                observed_url=str(row.get("source_url") or url),
                job_key=job.job_key,
                enrichment_id=enrichment_id,
                discovery_order=5,
                discovery_method="known_ats_url_pattern",
                now=timestamp,
                fetcher=fetch_client,
            )
            if candidate and evidence:
                candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                seen_urls.add(candidate.canonical_url)
                discovered.append(candidate)
                candidate_evidence[candidate.candidate_id] = evidence
            elif message:
                failure_states.append((state, message))

        if options.search_query_budget > 0 and options.external_page_budget > 0:
            plan = build_search_plan(job, config)
            searched_pages = 0
            for query in plan.queries[: options.search_query_budget]:
                response = provider.search(query, limit=options.search_results_per_query)
                if response.status == "failed":
                    failure_states.append(("retryable_failure", response.error_message))
                for search_candidate in response.candidates:
                    if searched_pages >= options.external_page_budget:
                        break
                    url = canonicalize_url(search_candidate.url)
                    if not url or url in seen_urls or not is_authoritative_candidate(url, config, company=job.company):
                        continue
                    searched_pages += 1
                    candidate, evidence, state, message, _retryable = _fetch_candidate(
                        url,
                        observed_url=search_candidate.url,
                        job_key=job.job_key,
                        enrichment_id=enrichment_id,
                        discovery_order=6,
                        discovery_method="controlled_external_search",
                        now=timestamp,
                        fetcher=fetch_client,
                    )
                    if candidate and evidence:
                        candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                        seen_urls.add(candidate.canonical_url)
                        discovered.append(candidate)
                        candidate_evidence[candidate.candidate_id] = evidence
                    elif message:
                        failure_states.append((state, message))

        manual_decision = prior.manual_resolution_decision
        manual_url = prior.manual_authoritative_url
        invalid_manual_reason = ""
        if manual_decision not in MANUAL_DECISIONS:
            invalid_manual_reason = (
                "Invalid manual_resolution_decision. Use accept, replace, remove, reject_automated, or blank"
            )
        elif manual_decision in {"accept", "replace", "remove", "reject_automated"} and (
            not prior.manual_reviewer or not prior.manual_review_date
        ):
            invalid_manual_reason = "Manual decisions require manual_reviewer and manual_review_date"
        elif manual_decision in {"accept", "replace"} and not manual_url:
            invalid_manual_reason = "Manual accept or replace requires manual_authoritative_url"

        if invalid_manual_reason:
            failure_states.append(("blocked", invalid_manual_reason))
        elif manual_decision == "remove":
            if manual_url:
                removed = ResolutionCandidate(
                    job_key=job.job_key,
                    discovery_order=7,
                    discovery_method="manual_override",
                    observed_url=manual_url,
                    canonical_url=canonicalize_url(manual_url),
                    candidate_state="manual_removed",
                    rejection_reason=f"Removed by {prior.manual_reviewer or 'manual reviewer'} on {prior.manual_review_date or timestamp[:10]}",
                    discovered_at=timestamp,
                    updated_at=timestamp,
                )
                _upsert_record(sheet_client, "Resolution_Candidates", "candidate_id", removed.to_dict(), candidate_index)
                summary.candidate_rows_written += 1
            prior.manual_authoritative_url = ""
            prior.manual_resolution_decision = ""
            prior.manual_reviewer = ""
            prior.manual_review_date = ""
            manual_url = ""
            manual_decision = ""
        elif manual_decision in {"accept", "replace"} and manual_url:
            candidate, evidence, state, message, _retryable = _fetch_candidate(
                manual_url,
                observed_url=manual_url,
                job_key=job.job_key,
                enrichment_id=enrichment_id,
                discovery_order=7,
                discovery_method="manual_override",
                now=timestamp,
                fetcher=fetch_client,
            )
            if candidate and evidence:
                candidate = apply_score(candidate, score_candidate(job, candidate, config=config, thresholds=options.thresholds))
                if candidate.candidate_state != "rejected":
                    candidate.candidate_state = "manual_override"
                    candidate.match_confidence = max(candidate.match_confidence, options.thresholds.authoritative)
                    discovered.append(candidate)
                    candidate_evidence[candidate.candidate_id] = evidence
                else:
                    failure_states.append(("blocked", candidate.rejection_reason or "Manual URL did not pass validation"))
            elif message:
                failure_states.append((state, message))

        discovered = _dedupe_candidates(discovered)[: options.maximum_candidates_per_job]
        for candidate in discovered:
            candidate.resolution_id = prior.resolution_id
            candidate.updated_at = timestamp
            summary.candidate_rows_written += int(
                _upsert_record(sheet_client, "Resolution_Candidates", "candidate_id", candidate.to_dict(), candidate_index)
            )
        summary.candidates_discovered += len(discovered)

        manual_candidate = next((item for item in discovered if item.candidate_state == "manual_override"), None)
        if invalid_manual_reason:
            state, selected, detail = "ambiguous", max(discovered, key=lambda item: item.match_confidence, default=None), invalid_manual_reason
        elif manual_candidate is not None:
            state, selected, detail = "manual_override", manual_candidate, ""
        elif manual_decision == "reject_automated":
            state, selected, detail = "ambiguous", max(discovered, key=lambda item: item.match_confidence, default=None), "Automated resolution was rejected by manual review"
        else:
            state, selected, detail = _select_state(discovered, options)

        if selected is None and failure_states and not invalid_manual_reason:
            state_order = {"retryable_failure": 4, "blocked": 3, "unsupported": 2, "not_found": 1}
            state, detail = max(failure_states, key=lambda pair: state_order.get(pair[0], 0))
        blocker = ""
        if state == "ambiguous":
            blocker = "manual_review_required"
        elif state == "resolved_probable":
            blocker = "authoritative_match_below_threshold"
        elif state == "not_found":
            blocker = "no_authoritative_url"
        elif state == "blocked":
            blocker = "source_blocked"
        elif state == "unsupported":
            blocker = "no_supported_enrichment_path"
        elif state == "retryable_failure":
            blocker = "retry_scheduled"

        resolution = _resolution_from_selection(
            prior,
            state=state,
            candidate=selected,
            candidate_count=len(discovered),
            attempted_at=timestamp,
            blocker_reason=blocker,
            error_message=detail,
            latency_seconds=round(perf_counter() - job_started),
        )

        if selected is not None and state in {"resolved_authoritative", "manual_override"}:
            evidence = candidate_evidence.get(selected.candidate_id)
            if evidence is None:
                resolution.resolution_state = "resolved_probable"
                resolution.blocker_reason = "manual_review_required"
                resolution.error_message = "Selected candidate has no retrievable posting evidence"
                state = resolution.resolution_state
            else:
                evidence.accepted = True
                evidence.match_confidence = selected.match_confidence
                summary.evidence_written += int(
                    _upsert_record(sheet_client, "Enrichment_Evidence", "evidence_id", evidence.to_dict(), evidence_index)
                )
                job, changed = merge_verified_evidence(
                    job,
                    evidence,
                    match_confidence=selected.match_confidence,
                    evidence_rules=priority_rules,
                    completed_at=timestamp,
                )
                _update_job(sheet_client, job_row, job)
                summary.jobs_updated += int(bool(changed) or job.enrichment_status in {"enriched", "partial"})
                source = _source_record(job, selected, timestamp)
                summary.source_rows_written += int(
                    _upsert_record(sheet_client, "Job_Sources", "source_key", source, source_index)
                )
                queue_target = queue_by_job.get(job.job_key)
                if queue_target is not None:
                    queue_row, item = queue_target
                    item.current_stage = "resolution"
                    item.status = job.enrichment_status
                    item.matched_url = selected.canonical_url
                    item.match_confidence = selected.match_confidence
                    item.fields_recovered = ", ".join(evidence.recovered_fields())
                    item.error_type = ""
                    item.error_message = ""
                    item.last_attempted_at = timestamp
                    item.updated_at = timestamp
                    sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())
                selected.accepted = True
                selected.candidate_state = "accepted"
                selected.rejection_reason = ""
                summary.candidate_rows_written += int(
                    _upsert_record(sheet_client, "Resolution_Candidates", "candidate_id", selected.to_dict(), candidate_index)
                )

        summary.resolution_rows_written += int(
            _upsert_record(sheet_client, "Posting_Resolution", "resolution_id", resolution.to_dict(), resolution_index)
        )
        resolutions[job.job_key] = (resolutions.get(job.job_key, (0, prior))[0], resolution)
        summary.resolution_succeeded += int(resolution.resolution_state in {"resolved_authoritative", "manual_override"})
        summary.resolved_authoritative += int(resolution.resolution_state == "resolved_authoritative")
        summary.resolved_probable += int(resolution.resolution_state == "resolved_probable")
        summary.ambiguous += int(resolution.resolution_state == "ambiguous")
        summary.not_found += int(resolution.resolution_state == "not_found")
        summary.blocked += int(resolution.resolution_state == "blocked")
        summary.unsupported += int(resolution.resolution_state == "unsupported")
        summary.retryable_failures += int(resolution.resolution_state == "retryable_failure")
        summary.manual_overrides += int(resolution.resolution_state == "manual_override")
        summary.manual_intervention_required += int(
            resolution.resolution_state in {"resolved_probable", "ambiguous", "blocked", "unsupported"}
        )

    return summary


def preview_posting_resolution(sheet_client: Any, *, job_key: str = "", limit: int = 50) -> dict[str, Any]:
    configs = load_company_configs(sheet_client)
    target_rows = [record for _, record in _records(sheet_client, "Target_Companies")]
    resolutions = {
        str(record.get("job_key") or ""): PostingResolution.from_dict(record)
        for _, record in _records(sheet_client, "Posting_Resolution")
        if str(record.get("job_key") or "")
    }
    evidence_by_job: dict[str, bool] = {}
    for _, row in _records(sheet_client, "Enrichment_Evidence"):
        key = str(row.get("job_key") or "")
        evidence_by_job[key] = evidence_by_job.get(key, False) or _truthy(row.get("accepted"))
    jobs = []
    for _, job in _jobs(sheet_client):
        if job_key and job.job_key != job_key:
            continue
        manual_action = bool((resolutions.get(job.job_key) or PostingResolution()).manual_resolution_decision)
        if job.status not in OPEN_STATUSES or job.score_status == "excluded" or job.potential_priority == "excluded":
            continue
        if job.score_status == "verified" and not manual_action:
            continue
        config = resolve_company_config(job.company, configs)
        jobs.append(
            {
                "job_key": job.job_key,
                "company": job.company,
                "title": job.title,
                "potential_priority": job.potential_priority,
                "target_company": _company_target(job, target_rows),
                "prior_partial_evidence": evidence_by_job.get(job.job_key, False),
                "career_domain": config.career_domain if config else "",
                "ats_platform": config.ats_platform if config else "",
                "configured_search_url": config.career_search_url if config else "",
                "manual_resolution_decision": (resolutions.get(job.job_key) or PostingResolution()).manual_resolution_decision,
            }
        )
    jobs.sort(
        key=lambda row: (
            0 if row["potential_priority"] == "high" else 1 if row["target_company"] else 2,
            row["company"],
            row["title"],
        )
    )
    return {"eligible_jobs": len(jobs), "jobs": jobs[: max(0, limit)]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve sparse leads to authoritative employer or ATS postings")
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--run", action="store_true")
    execution.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--job-key", default="")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--no-web-search", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise
    from src.settings import load_settings
    from src.sheets import SheetClient

    client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        validate_workbook_or_raise(client)
        print(json.dumps(preview_posting_resolution(client, job_key=args.job_key, limit=args.limit), indent=2))
        return
    migration = migrate_trailing_headers(client)
    if not migration.ok:
        raise RuntimeError("Workbook schema migration did not produce a valid workbook")
    provider: SearchProvider = DisabledSearchProvider() if args.no_web_search else DuckDuckGoHtmlSearchProvider()
    settings = ResolutionSettings.from_yaml(args.config)
    print(
        json.dumps(
            run_posting_resolution(
                client,
                limit=max(0, args.limit),
                job_key=args.job_key,
                settings=settings,
                search_provider=provider,
            ).to_dict(),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
