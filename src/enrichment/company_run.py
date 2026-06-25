from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from src.enrichment.ats import AtsCandidate, AtsDiscoveryResult, discover_ats_candidates
from src.enrichment.company_config import CompanyEnrichmentConfig, load_company_configs, resolve_company_config
from src.enrichment.matcher import assess_match
from src.enrichment.merge import merge_verified_evidence
from src.enrichment.models import EnrichmentEvidence, EnrichmentQueueItem, MatchResult, utc_now_iso
from src.enrichment.queue import evidence_id_for, priority_sort_key
from src.models import JobPosting

DEFAULT_PRIORITY_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "potential_priority_rules.yml"
COMPANY_STAGE_INPUT_STATUSES = {"not_found", "ambiguous", "permanent_failure"}
OPEN_STATUSES = {"open", "reopened"}


@dataclass(slots=True)
class CompanyEnrichmentRunSummary:
    jobs_evaluated: int = 0
    company_ats_attempts: int = 0
    configs_missing: int = 0
    configured_only: int = 0
    candidates_found: int = 0
    enriched: int = 0
    partial: int = 0
    ambiguous: int = 0
    not_found: int = 0
    failures: int = 0
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


def _content_hash(candidate: AtsCandidate) -> str:
    return hashlib.sha256(json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _candidate_evidence(candidate: AtsCandidate, item: EnrichmentQueueItem, config: CompanyEnrichmentConfig, now: str) -> EnrichmentEvidence:
    content_hash = _content_hash(candidate)
    source_url = candidate.url or config.career_search_url or config.source_url
    evidence = EnrichmentEvidence(
        job_key=item.job_key,
        enrichment_id=item.enrichment_id,
        source_type=f"{candidate.platform}_ats",
        source_url=source_url,
        retrieved_at=now,
        http_status=200,
        canonical_url=candidate.url,
        source_title=candidate.title,
        source_company=candidate.company or config.canonical_name,
        source_location=candidate.location,
        description_text=candidate.description_text,
        salary_min=candidate.salary_min,
        salary_max=candidate.salary_max,
        currency=candidate.currency or "USD",
        employment_type=candidate.employment_type,
        remote_status=candidate.remote_status,
        work_model=candidate.work_model,
        posting_date=candidate.posting_date,
        valid_through=candidate.valid_through,
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(item.enrichment_id, source_url, content_hash)
    return evidence


def _failure_evidence(item: EnrichmentQueueItem, source_url: str, now: str, error_type: str, message: str) -> EnrichmentEvidence:
    content_hash = hashlib.sha256(f"company_ats:{error_type}:{source_url}:{message}".encode()).hexdigest()
    evidence = EnrichmentEvidence(
        job_key=item.job_key,
        enrichment_id=item.enrichment_id,
        source_type="company_ats_failure",
        source_url=source_url or item.lead_url,
        retrieved_at=now,
        accepted=False,
        rejection_reason=f"{error_type}: {message}"[:1000],
        raw_content_hash=content_hash,
    )
    evidence.evidence_id = evidence_id_for(item.enrichment_id, evidence.source_url, content_hash)
    return evidence


def _eligible(item: EnrichmentQueueItem, job: JobPosting) -> bool:
    return (
        item.current_stage == "direct_url"
        and item.status in COMPANY_STAGE_INPUT_STATUSES
        and job.status in OPEN_STATUSES
        and job.score_status in {"provisional", "partially_verified"}
        and job.potential_priority in {"high", "medium"}
    )


def _fail(
    item: EnrichmentQueueItem,
    job: JobPosting,
    summary: CompanyEnrichmentRunSummary,
    *,
    error_type: str,
    message: str,
    source_url: str,
) -> None:
    item.status = "not_found"
    item.matched_url = source_url
    item.error_type = error_type
    item.error_message = message[:1000]
    job.enrichment_status = "not_found"
    job.enrichment_source_url = source_url
    summary.not_found += 1


def _rank(job: JobPosting, rows: Iterable[EnrichmentEvidence]) -> list[tuple[MatchResult, EnrichmentEvidence]]:
    ranked = [(assess_match(job, evidence), evidence) for evidence in rows]
    ranked.sort(key=lambda pair: (pair[0].confidence, len(pair[1].description_text)), reverse=True)
    return ranked


def run_company_ats_enrichment(
    sheet_client: Any,
    *,
    limit: int = 10,
    now: str | None = None,
    job_key: str = "",
    configs: Iterable[CompanyEnrichmentConfig] | None = None,
    priority_rules: dict[str, Any] | None = None,
    discovery: Callable[..., AtsDiscoveryResult] = discover_ats_candidates,
    session: Any | None = None,
) -> CompanyEnrichmentRunSummary:
    timestamp = now or utc_now_iso()
    if priority_rules is None:
        from src.potential_priority import load_potential_priority_rules

        priority_rules = load_potential_priority_rules(DEFAULT_PRIORITY_RULES_PATH)
    company_configs = list(configs) if configs is not None else load_company_configs(sheet_client)
    summary = CompanyEnrichmentRunSummary()
    job_rows = [(row, job) for row, job in _jobs(sheet_client) if not job_key or job.job_key == job_key]
    job_by_key = {job.job_key: (row, job) for row, job in job_rows if job.job_key}
    queue_rows = [(row, EnrichmentQueueItem.from_dict(record)) for row, record in _records(sheet_client, "Enrichment_Queue")]
    due = [pair for pair in queue_rows if pair[1].job_key in job_by_key and _eligible(pair[1], job_by_key[pair[1].job_key][1])]
    due.sort(key=lambda pair: priority_sort_key(pair[1]))
    evidence_by_id = _existing_evidence(sheet_client)

    for queue_row, item in due[: max(0, limit)]:
        summary.jobs_evaluated += 1
        job_row, job = job_by_key[item.job_key]
        config = resolve_company_config(job.company, company_configs)
        item.current_stage = "company_ats"
        item.status = "in_progress"
        item.attempt_count += 1
        item.last_attempted_at = timestamp
        item.next_attempt_at = ""
        item.matched_url = ""
        item.match_confidence = None
        item.fields_recovered = ""
        item.error_type = ""
        item.error_message = ""
        sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())
        job.enrichment_status = "in_progress"
        _update_job(sheet_client, job_row, job)

        result: AtsDiscoveryResult | None = None
        evidence_before = summary.evidence_written
        if config is None:
            summary.configs_missing += 1
            _fail(item, job, summary, error_type="company_config_missing", message="No unique active company enrichment configuration matched this employer", source_url=item.lead_url)
        else:
            summary.company_ats_attempts += 1
            try:
                result = discovery(config, expected_title=job.title, expected_location=job.location, session=session)
            except Exception as exc:
                result = AtsDiscoveryResult(config.ats_platform or "unknown", "failed", error_message=str(exc), search_url=config.career_search_url)
            summary.candidates_found += len(result.candidates)
            search_url = result.search_url or config.career_search_url or config.source_url
            if result.status == "configured_only":
                summary.configured_only += 1
                _fail(item, job, summary, error_type="configured_adapter_required", message=result.error_message, source_url=search_url)
            elif result.status in {"failed", "invalid_config"}:
                summary.failures += 1
                error_type = "ats_discovery_failed" if result.status == "failed" else "ats_config_invalid"
                _fail(item, job, summary, error_type=error_type, message=result.error_message, source_url=search_url)
            elif not result.candidates:
                _fail(item, job, summary, error_type="ats_no_candidates", message="The configured company source returned no job postings", source_url=search_url)
            else:
                evidence_rows = [_candidate_evidence(candidate, item, config, timestamp) for candidate in result.candidates if candidate.title and (candidate.url or candidate.description_text)]
                ranked = _rank(job, evidence_rows)
                accepted = [pair for pair in ranked if pair[0].accepted]
                plausible = [pair for pair in ranked if pair[0].confidence >= 60]
                if len(accepted) == 1:
                    match, evidence = accepted[0]
                    evidence.accepted = True
                    evidence.match_confidence = match.confidence
                    summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
                    job, changed = merge_verified_evidence(job, evidence, match_confidence=match.confidence, evidence_rules=priority_rules, completed_at=timestamp)
                    item.status = job.enrichment_status
                    item.matched_url = evidence.canonical_url or evidence.source_url
                    item.match_confidence = match.confidence
                    item.fields_recovered = ", ".join(evidence.recovered_fields())
                    summary.enriched += int(item.status == "enriched")
                    summary.partial += int(item.status == "partial")
                    summary.jobs_updated += int(bool(changed) or item.status in {"enriched", "partial"})
                elif len(accepted) > 1 or plausible:
                    selections = (accepted if len(accepted) > 1 else plausible)[:5]
                    for match, evidence in selections:
                        evidence.accepted = False
                        evidence.match_confidence = match.confidence
                        evidence.rejection_reason = "multiple plausible company or ATS postings require review"
                        summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))
                    best_match, best_evidence = selections[0]
                    item.status = "ambiguous"
                    item.matched_url = best_evidence.canonical_url or best_evidence.source_url
                    item.match_confidence = best_match.confidence
                    item.fields_recovered = ", ".join(best_evidence.recovered_fields())
                    item.error_type = "ambiguous_company_ats_match"
                    item.error_message = "Multiple or lower-confidence company postings require manual review"
                    job.enrichment_status = "ambiguous"
                    job.enrichment_source_url = item.matched_url
                    job.enrichment_match_confidence = best_match.confidence
                    summary.ambiguous += 1
                else:
                    _fail(item, job, summary, error_type="ats_match_not_found", message="Configured company postings did not pass match validation", source_url=search_url)
                    if ranked:
                        match, evidence = ranked[0]
                        evidence.accepted = False
                        evidence.match_confidence = match.confidence
                        evidence.rejection_reason = "; ".join(match.reasons)
                        item.matched_url = evidence.canonical_url or evidence.source_url or search_url
                        item.match_confidence = match.confidence
                        summary.evidence_written += int(_write_evidence(sheet_client, evidence, evidence_by_id))

        if item.status == "not_found" and summary.evidence_written == evidence_before:
            failure = _failure_evidence(item, item.matched_url or item.lead_url, timestamp, item.error_type, item.error_message)
            summary.evidence_written += int(_write_evidence(sheet_client, failure, evidence_by_id))
        item.updated_at = timestamp
        job.enrichment_last_attempted_at = timestamp
        if hasattr(job, "refresh_updated_at"):
            job.refresh_updated_at()
        sheet_client.update_record("Enrichment_Queue", queue_row, item.to_dict())
        _update_job(sheet_client, job_row, job)

    return summary


def preview_company_ats_queue(sheet_client: Any, *, job_key: str = "") -> dict[str, Any]:
    configs = load_company_configs(sheet_client)
    jobs = {job.job_key: job for _, job in _jobs(sheet_client) if job.job_key and (not job_key or job.job_key == job_key)}
    rows = []
    for _, record in _records(sheet_client, "Enrichment_Queue"):
        item = EnrichmentQueueItem.from_dict(record)
        job = jobs.get(item.job_key)
        if job is None or not _eligible(item, job):
            continue
        config = resolve_company_config(job.company, configs)
        rows.append({
            "job_key": job.job_key,
            "company": job.company,
            "title": job.title,
            "platform": config.ats_platform if config else "",
            "career_search_url": config.career_search_url if config else "",
            "configuration_status": "configured" if config else "missing",
        })
    return {"eligible_jobs": len(rows), "jobs": rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process company career-site and ATS enrichment after direct-link failure")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--job-key", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.run and not args.dry_run:
        raise SystemExit("Choose --run or --dry-run")
    from src.settings import load_settings
    from src.sheets import SheetClient

    sheet_client = SheetClient.from_settings(load_settings())
    if args.dry_run:
        print(json.dumps(preview_company_ats_queue(sheet_client, job_key=args.job_key), indent=2))
        return
    from src.schema import migrate_trailing_headers, validate_workbook_or_raise

    migrate_trailing_headers(sheet_client)
    validate_workbook_or_raise(sheet_client)
    print(json.dumps(run_company_ats_enrichment(sheet_client, limit=args.limit, job_key=args.job_key).to_dict(), indent=2))


if __name__ == "__main__":
    main()
