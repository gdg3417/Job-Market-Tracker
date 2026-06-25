from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from src.enrichment.company_config import CompanyEnrichmentConfig, load_company_configs, resolve_company_config
from src.enrichment.extractors import extract_job_evidence
from src.enrichment.fetcher import DirectLinkFetcher, EnrichmentFetchError
from src.enrichment.matcher import assess_match
from src.enrichment.merge import merge_verified_evidence
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem, MatchResult, utc_now_iso
from src.enrichment.queue import evidence_id_for, priority_sort_key
from src.enrichment.search import (
    DEFAULT_CANDIDATE_PAGE_BUDGET,
    DEFAULT_QUERY_BUDGET,
    DEFAULT_RESULTS_PER_QUERY,
    DisabledSearchProvider,
    DuckDuckGoHtmlSearchProvider,
    SearchCacheRecord,
    SearchCandidate,
    SearchPlan,
    SearchProvider,
    SearchResponse,
    build_search_plan,
    candidate_authority_rank,
    clean_text,
    duckduckgo_search_url,
    encode_result_urls,
    is_authoritative_candidate,
    normalize_candidate_url,
    query_id_for,
)
from src.models import JobPosting

DEFAULT_PRIORITY_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "potential_priority_rules.yml"
SEARCH_STAGE_INPUT_STATUSES = {"not_found", "ambiguous", "permanent_failure"}
OPEN_STATUSES = {"open", "reopened"}


@dataclass(slots=True)
class ExternalSearchRunSummary:
    jobs_evaluated: int = 0
    search_attempts: int = 0
    cache_hits: int = 0
    queries_executed: int = 0
    search_failures: int = 0
    candidates_discovered: int = 0
    candidates_filtered: int = 0
    candidate_pages_fetched: int = 0
    candidate_pages_rejected: int = 0
    enriched: int = 0
    partial: int = 0
    ambiguous: int = 0
    not_found: int = 0
    evidence_written: int = 0
    jobs_updated: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _records(sheet_client: Any, worksheet: str) -> list[tuple[int, dict[str, Any]]]:
    if hasattr(sheet_client, "read_records_with_row_numbers"):
        return list(sheet_client.read_records_with_row_numbers(worksheet))
    return [(index + 2, row) for index, row in enumerate(sheet_client.read_records(worksheet))]


def _jobs(sheet_client: Any) -> list[tuple[int, JobPosting]]:
    if hasattr(sheet_client, "read_jobs_with_row_numbers"):
        return list(sheet_client.read_jobs_with_row_numbers())
    return [(row_number, JobPosting.from_dict(row)) for row_number, row in _records(sheet_client, "Jobs")]


def _update_job(sheet_client: Any, row_number: int, job: JobPosting) -> None:
    if hasattr(sheet_client, "update_job"):
        sheet_client.update_job(row_number, job)
    else:
        sheet_client.update_record("Jobs", row_number, job.to_dict())


def _existing_evidence(sheet_client: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    return {
        str(row.get("evidence_id") or ""): (row_number, row)
        for row_number, row in _records(sheet_client, "Enrichment_Evidence")
        if str(row.get("evidence_id") or "")
    }


def _write_evidence(sheet_client: Any, evidence: EnrichmentEvidence, existing: dict[str, tuple[int, dict[str, Any]]]) -> bool:
    record = evidence.to_dict()
    current = existing.get(evidence.evidence_id)
    if current is None:
        sheet_client.append_record("Enrichment_Evidence", record)
        row_number = max((number for number, _ in existing.values()), default=1) + 1
        existing[evidence.evidence_id] = (row_number, record)
        return True
    row_number, prior = current
    if prior == record:
        return False
    sheet_client.update_record("Enrichment_Evidence", row_number, record)
    existing[evidence.evidence_id] = (row_number, record)
    return True


def _search_cache_from_evidence(
    evidence_rows: dict[str, tuple[int, dict[str, Any]]],
) -> dict[str, SearchCacheRecord]:
    cache: dict[str, SearchCacheRecord] = {}
    for _, row in evidence_rows.values():
        evidence = EnrichmentEvidence.from_dict(row)
        if evidence.source_type != "external_search_discovery" or not evidence.source_title:
            continue
        provider = evidence.source_company or "duckduckgo_html"
        status_match = re.search(r"status=([^;]+)", evidence.rejection_reason)
        status = clean_text(status_match.group(1)) if status_match else "success"
        record = SearchCacheRecord(
            query_id=query_id_for(provider, evidence.source_title),
            job_key=evidence.job_key,
            enrichment_id=evidence.enrichment_id,
            provider=provider,
            query_text=evidence.source_title,
            search_url=evidence.source_url,
            searched_at=evidence.retrieved_at,
            status=status,
            result_urls=evidence.description_text,
            error_message=evidence.rejection_reason.partition("error=")[2][:1000],
        )
        current = cache.get(record.query_id)
        if current is None or clean_text(record.searched_at) > clean_text(current.searched_at):
            cache[record.query_id] = record
    return cache


def _eligible(
    item: EnrichmentQueueItem,
    job: JobPosting,
    *,
    allow_external_replay: bool = False,
) -> bool:
    eligible_stages = {"company_ats"}
    if allow_external_replay:
        eligible_stages.add("external_search")
    return (
        item.current_stage in eligible_stages
        and item.status in SEARCH_STAGE_INPUT_STATUSES
        and job.status in OPEN_STATUSES
        and job.score_status in {"provisional", "partially_verified"}
        and job.potential_priority in {"high", "medium"}
    )


def _discovery_evidence(item: EnrichmentQueueItem, response: SearchResponse, now: str) -> EnrichmentEvidence:
    material = json.dumps(
        {
            "provider": response.provider,
            "query": response.query,
            "search_url": response.search_url,
            "status": response.status,
            "urls": [candidate.url for candidate in response.candidates],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    evidence = EnrichmentEvidence(
        job_key=item.job_key,
        enrichment_id=item.enrichment_id,
        source_type="external_search_discovery",
        source_url=response.search_url,
        retrieved_at=now,
        source_title=response.query,
        source_company=response.provider,
        description_text=encode_result_urls(candidate.url for candidate in response.candidates),
        accepted=False,
        rejection_reason=(
            "Discovery only. Search result titles and snippets are not verified scoring evidence; "
            f"status={response.status}; error={response.error_message[:500]}"
        ),
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(item.enrichment_id, response.search_url, content_hash)
    return evidence


def _page_failure_evidence(
    item: EnrichmentQueueItem,
    *,
    source_url: str,
    now: str,
    error_type: str,
    message: str,
    status_code: int | None = None,
) -> EnrichmentEvidence:
    content_hash = hashlib.sha256(f"external:{source_url}:{error_type}:{status_code or ''}:{message}".encode("utf-8")).hexdigest()
    evidence = EnrichmentEvidence(
        job_key=item.job_key,
        enrichment_id=item.enrichment_id,
        source_type="external_search_candidate_failure",
        source_url=source_url,
        retrieved_at=now,
        http_status=status_code,
        accepted=False,
        rejection_reason=f"{error_type}: {message}"[:1000],
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(item.enrichment_id, source_url, content_hash)
    return evidence


def _dedupe_page_evidence(rows: Iterable[EnrichmentEvidence]) -> list[EnrichmentEvidence]:
    unique: list[EnrichmentEvidence] = []
    seen: set[str] = set()
    for evidence in rows:
        url_key = normalize_candidate_url(evidence.canonical_url or evidence.source_url)
        key = url_key or clean_text(evidence.raw_content_hash)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(evidence)
    return unique


def _rank(job: JobPosting, rows: Iterable[EnrichmentEvidence]) -> list[tuple[MatchResult, EnrichmentEvidence]]:
    ranked = [(assess_match(job, evidence), evidence) for evidence in rows]
    ranked.sort(key=lambda pair: (pair[0].confidence, len(pair[1].description_text)), reverse=True)
    return ranked


def _dedupe_candidate_urls(
    candidates: Iterable[SearchCandidate | str],
    *,
    config: CompanyEnrichmentConfig | None,
    company: str,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.url if isinstance(candidate, SearchCandidate) else candidate
        url = normalize_candidate_url(value)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    urls.sort(key=lambda url: candidate_authority_rank(url, config, company=company))
    return urls


def _cached_response(record: SearchCacheRecord) -> SearchResponse:
    return SearchResponse(
        provider=record.provider,
        query=record.query_text,
        search_url=record.search_url,
        status=record.status,
        candidates=[SearchCandidate(url=url, query=record.query_text, provider=record.provider) for url in record.urls],
        error_message=record.error_message,
        from_cache=True,
    )


def _search_responses(
    *,
    item: EnrichmentQueueItem,
    plan: SearchPlan,
    provider: SearchProvider,
    cache: dict[str, SearchCacheRecord],
    now: str,
    query_budget: int,
    results_per_query: int,
    summary: ExternalSearchRunSummary,
) -> list[SearchResponse]:
    responses: list[SearchResponse] = []
    remaining = max(0, query_budget)
    for query in plan.queries:
        query_id = query_id_for(provider.name, query)
        cached = cache.get(query_id)
        if cached is not None and cached.is_fresh(now=now):
            response = _cached_response(cached)
            summary.cache_hits += 1
        elif remaining <= 0:
            continue
        else:
            try:
                response = provider.search(query, limit=max(0, results_per_query))
            except Exception as exc:
                response = SearchResponse(
                    provider=provider.name,
                    query=query,
                    search_url=duckduckgo_search_url(query),
                    status="failed",
                    error_message=str(exc),
                )
            remaining -= 1
            summary.queries_executed += 1
            record = SearchCacheRecord(
                query_id=query_id,
                job_key=item.job_key,
                enrichment_id=item.enrichment_id,
                provider=response.provider,
                query_text=response.query,
                search_url=response.search_url,
                searched_at=now,
                status=response.status,
                result_urls=encode_result_urls(candidate.url for candidate in response.candidates),
                error_message=response.error_message[:1000],
            )
            cache[query_id] = record
        responses.append(response)
        summary.search_attempts += 1
        summary.search_failures += int(response.status == "failed")
        summary.candidates_discovered += len(response.candidates)
    return responses


def _set_manual_review_link(job: JobPosting, url: str) -> None:
    clean_url = clean_text(url)
    explanation = clean_text(job.score_explanation)
    parts = [part.strip() for part in explanation.split(";") if part.strip() and not part.strip().startswith("manual_review_url=")]
    if clean_url:
        parts.append(f"manual_review_url={clean_url}")
    job.score_explanation = "; ".join(parts)


def run_external_search_enrichment(
    sheet_client: Any,
    *,
    limit: int = 10,
    now: str | None = None,
    job_key: str = "",
    configs: Iterable[CompanyEnrichmentConfig] | None = None,
    priority_rules: dict[str, Any] | None = None,
    provider: SearchProvider | None = None,
    fetcher: DirectLinkFetcher | Any | None = None,
    query_budget: int = DEFAULT_QUERY_BUDGET,
    results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    candidate_page_budget: int = DEFAULT_CANDIDATE_PAGE_BUDGET,
    candidate_urls: Iterable[str] | None = None,
) -> ExternalSearchRunSummary:
    timestamp = now or utc_now_iso()
    if priority_rules is None:
        from src.potential_priority import load_potential_priority_rules

        priority_rules = load_potential_priority_rules(DEFAULT_PRIORITY_RULES_PATH)
    company_configs = list(configs) if configs is not None else load_company_configs(sheet_client)
    provider = provider or DuckDuckGoHtmlSearchProvider()
    fetcher = fetcher or DirectLinkFetcher()

    requested_candidate_urls = [clean_text(value) for value in candidate_urls or [] if clean_text(value)]
    supplied_urls: list[str] = []
    invalid_urls: list[str] = []
    for value in requested_candidate_urls:
        normalized = normalize_candidate_url(value)
        if not normalized:
            invalid_urls.append(value)
        elif normalized not in supplied_urls:
            supplied_urls.append(normalized)
    if invalid_urls:
        raise ValueError(f"Manual candidate URLs must be safe public HTTP or HTTPS URLs: {', '.join(invalid_urls)}")
    if requested_candidate_urls and not job_key:
        raise ValueError("Manual candidate URLs require an exact job_key")

    summary = ExternalSearchRunSummary()
    job_rows = [(row, job) for row, job in _jobs(sheet_client) if not job_key or job.job_key == job_key]
    job_by_key = {job.job_key: (row, job) for row, job in job_rows if job.job_key}
    queue_rows = [(row, EnrichmentQueueItem.from_dict(record)) for row, record in _records(sheet_client, "Enrichment_Queue")]
    due = [
        pair
        for pair in queue_rows
        if pair[1].job_key in job_by_key
        and _eligible(
            pair[1],
            job_by_key[pair[1].job_key][1],
            allow_external_replay=bool(supplied_urls),
        )
    ]
    due.sort(key=lambda pair: priority_sort_key(pair[1]))

    if supplied_urls:
        if job_key not in job_by_key:
            raise ValueError(f"Manual candidate validation job was not found: {job_key}")
        queue_for_job = [pair for pair in queue_rows if pair[1].job_key == job_key]
        if not queue_for_job:
            raise ValueError(f"Manual candidate validation requires an existing enrichment queue item for job_key={job_key}")
        if not due:
            _, current = queue_for_job[0]
            raise ValueError(
                "Manual candidate validation requires a terminal company_ats or external_search queue item; "
                f"current_stage={current.current_stage}, status={current.status}"
            )

    evidence_by_id = _existing_evidence(sheet_client)
    cache = _search_cache_from_evidence(evidence_by_id)

    for queue_row, item in due[: max(0, limit)]:
        summary.jobs_evaluated += 1
        job_row, job = job_by_key[item.job_key]
        config = resolve_company_config(job.company, company_configs)
        plan = build_search_plan(job, config)
        item.current_stage = "external_search"
        item.status = "in_progress"
        item.attempt_count += 1
        item.last_attempted_at = timestamp
        item.next_attempt_at = ""
        item.matched_url = ""
        item.match_confidence = None
        item.fields_recovered = ""
        item.error_type = ""
        item.error_message = ""
        item.updated_at = timestamp
        sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())
        job.enrichment_status = "in_progress"
        job.enrichment_last_attempted_at = timestamp
        _update_job(sheet_client, job_row, job)

        responses = [] if supplied_urls else _search_responses(
            item=item,
            plan=plan,
            provider=provider,
            cache=cache,
            now=timestamp,
            query_budget=query_budget,
            results_per_query=results_per_query,
            summary=summary,
        )
        for response in responses:
            if response.from_cache:
                continue
            discovery = _discovery_evidence(item, response, timestamp)
            summary.evidence_written += int(_write_evidence(sheet_client, discovery, evidence_by_id))

        raw_candidates: list[SearchCandidate | str] = list(supplied_urls)
        for response in responses:
            raw_candidates.extend(response.candidates)
        candidate_list = _dedupe_candidate_urls(raw_candidates, config=config, company=job.company)
        authoritative = [url for url in candidate_list if is_authoritative_candidate(url, config, company=job.company)]
        summary.candidates_filtered += len(candidate_list) - len(authoritative)

        page_evidence: list[EnrichmentEvidence] = []
        for url in authoritative[: max(0, candidate_page_budget)]:
            try:
                fetched = fetcher.fetch(url)
                summary.candidate_pages_fetched += 1
                if not is_authoritative_candidate(fetched.final_url, config, company=job.company):
                    summary.candidate_pages_rejected += 1
                    failure = _page_failure_evidence(
                        item,
                        source_url=fetched.final_url,
                        now=timestamp,
                        error_type="non_authoritative_redirect",
                        message="External search candidate redirected outside the configured company or supported ATS domains",
                        status_code=fetched.status_code,
                    )
                    summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))
                    continue
                evidence = extract_job_evidence(
                    fetched,
                    job_key=item.job_key,
                    enrichment_id=item.enrichment_id,
                    retrieved_at=timestamp,
                )
                if evidence is None:
                    summary.candidate_pages_rejected += 1
                    failure = _page_failure_evidence(
                        item,
                        source_url=fetched.final_url,
                        now=timestamp,
                        error_type="non_job_page",
                        message="External search candidate was not a specific job posting",
                        status_code=fetched.status_code,
                    )
                    summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))
                    continue
                evidence_url = evidence.canonical_url or evidence.source_url
                if not is_authoritative_candidate(evidence_url, config, company=job.company):
                    summary.candidate_pages_rejected += 1
                    failure = _page_failure_evidence(
                        item,
                        source_url=evidence_url or fetched.final_url,
                        now=timestamp,
                        error_type="non_authoritative_canonical_url",
                        message="Validated page declared a canonical URL outside the configured company or supported ATS domains",
                        status_code=fetched.status_code,
                    )
                    summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))
                    continue
                evidence.source_type = "external_search_page"
                page_evidence.append(evidence)
            except EnrichmentFetchError as exc:
                summary.candidate_pages_rejected += 1
                failure = _page_failure_evidence(
                    item,
                    source_url=exc.final_url or url,
                    now=timestamp,
                    error_type=exc.error_type,
                    message=str(exc),
                    status_code=exc.status_code,
                )
                summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))
            except Exception as exc:
                summary.candidate_pages_rejected += 1
                failure = _page_failure_evidence(
                    item,
                    source_url=url,
                    now=timestamp,
                    error_type="candidate_processing_error",
                    message=str(exc),
                )
                summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))

        page_evidence = _dedupe_page_evidence(page_evidence)
        ranked = _rank(job, page_evidence)
        accepted = [pair for pair in ranked if pair[0].accepted]
        plausible = [pair for pair in ranked if pair[0].confidence >= 60]
        manual_url = plan.preferred_manual_url

        if len(accepted) == 1 and len(plausible) == 1:
            match, evidence = accepted[0]
            evidence.accepted = True
            evidence.match_confidence = match.confidence
            evidence.rejection_reason = ""
            evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, evidence.raw_content_hash)
            summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
            job, changed = merge_verified_evidence(
                job,
                evidence,
                match_confidence=match.confidence,
                evidence_rules=priority_rules,
                completed_at=timestamp,
            )
            item.status = job.enrichment_status
            item.matched_url = evidence.canonical_url or evidence.source_url
            item.match_confidence = match.confidence
            item.fields_recovered = ", ".join(evidence.recovered_fields())
            summary.enriched += int(item.status == "enriched")
            summary.partial += int(item.status == "partial")
            _set_manual_review_link(job, "")
            summary.jobs_updated += int(bool(changed) or item.status in {"enriched", "partial"})
        elif plausible:
            selections = plausible[:5]
            for match, evidence in selections:
                evidence.accepted = False
                evidence.match_confidence = match.confidence
                evidence.rejection_reason = "External search candidate requires review because the match was not uniquely authoritative"
                evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, evidence.raw_content_hash)
                summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
            best_match, best_evidence = selections[0]
            item.status = "ambiguous"
            item.matched_url = best_evidence.canonical_url or best_evidence.source_url or manual_url
            item.match_confidence = best_match.confidence
            item.fields_recovered = ", ".join(best_evidence.recovered_fields())
            item.error_type = "ambiguous_external_search_match"
            item.error_message = "One or more plausible external-search candidates require manual review"
            job.enrichment_status = "ambiguous"
            job.enrichment_source_url = item.matched_url
            job.enrichment_match_confidence = best_match.confidence
            _set_manual_review_link(job, item.matched_url or manual_url)
            summary.ambiguous += 1
            summary.jobs_updated += 1
        else:
            for match, evidence in ranked[:5]:
                evidence.accepted = False
                evidence.match_confidence = match.confidence
                evidence.rejection_reason = "; ".join(match.reasons)
                evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, evidence.raw_content_hash)
                summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
            best_confidence = ranked[0][0].confidence if ranked else 0
            item.status = "not_found"
            item.matched_url = manual_url
            item.match_confidence = best_confidence
            item.error_type = "external_search_match_not_found"
            if not authoritative:
                item.error_message = "No authoritative company or ATS posting URL was discovered. Use the manual review link."
            elif not page_evidence:
                item.error_message = "Authoritative candidates could not be validated as specific job postings. Use the manual review link."
            else:
                item.error_message = "External-search candidate pages did not reach the 60-point review threshold."
            job.enrichment_status = "not_found"
            job.enrichment_source_url = manual_url
            job.enrichment_match_confidence = best_confidence
            _set_manual_review_link(job, manual_url)
            summary.not_found += 1
            summary.jobs_updated += 1

        item.updated_at = timestamp
        job.enrichment_last_attempted_at = timestamp
        if hasattr(job, "refresh_updated_at"):
            job.refresh_updated_at()
        sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())
        _update_job(sheet_client, job_row, job)

    return summary


def preview_external_search_queue(sheet_client: Any, *, job_key: str = "") -> dict[str, Any]:
    configs = load_company_configs(sheet_client)
    jobs = {job.job_key: job for _, job in _jobs(sheet_client) if job.job_key and (not job_key or job.job_key == job_key)}
    rows = []
    for _, record in _records(sheet_client, "Enrichment_Queue"):
        item = EnrichmentQueueItem.from_dict(record)
        job = jobs.get(item.job_key)
        if job is None or not _eligible(item, job):
            continue
        config = resolve_company_config(job.company, configs)
        plan = build_search_plan(job, config)
        rows.append(
            {
                "job_key": job.job_key,
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "queries": list(plan.queries),
                "manual_links": [{"label": label, "url": url} for label, url in plan.manual_links],
                "preferred_manual_url": plan.preferred_manual_url,
            }
        )
    return {"eligible_jobs": len(rows), "jobs": rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run zero-cost external search fallback and validate authoritative posting matches")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--job-key", default="")
    parser.add_argument("--query-budget", type=int, default=DEFAULT_QUERY_BUDGET)
    parser.add_argument("--results-per-query", type=int, default=DEFAULT_RESULTS_PER_QUERY)
    parser.add_argument("--candidate-page-budget", type=int, default=DEFAULT_CANDIDATE_PAGE_BUDGET)
    parser.add_argument("--candidate-url", action="append", default=[], help="Validate a manually discovered candidate URL; repeatable and requires --job-key")
    parser.add_argument("--no-web-search", action="store_true", help="Generate manual links without querying DuckDuckGo")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.run and not args.dry_run:
        raise SystemExit("Choose --run or --dry-run")
    if args.candidate_url and not args.job_key:
        raise SystemExit("--candidate-url requires an exact --job-key")

    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        print(json.dumps(preview_external_search_queue(sheet_client, job_key=args.job_key), indent=2))
        return

    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    provider: SearchProvider = DisabledSearchProvider() if args.no_web_search else DuckDuckGoHtmlSearchProvider()
    print(
        json.dumps(
            run_external_search_enrichment(
                sheet_client,
                limit=args.limit,
                job_key=args.job_key,
                provider=provider,
                query_budget=0 if args.no_web_search else args.query_budget,
                results_per_query=args.results_per_query,
                candidate_page_budget=args.candidate_page_budget,
                candidate_urls=args.candidate_url,
            ).to_dict(),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
