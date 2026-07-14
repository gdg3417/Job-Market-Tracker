from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from src.models import utc_now_iso
from src.normalize import clean_text, normalize_url
from src.settings import load_settings
from src.sheets import SheetClient
from src.source_quality import (
    AUDIT_CLASSIFICATIONS,
    DEFAULT_WINDOW_WEEKS,
    SourceYieldRow,
    apply_approved_source_updates,
    audit_static_sources,
    build_run_record,
    build_source_yield_report,
    write_source_quality_surfaces,
)


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _normalized(value: Any) -> str:
    return clean_text(value).strip().lower().replace("-", "_").replace(" ", "_")


def _strategic_companies(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {
        clean_text(row.get("company_name")).lower()
        for row in rows
        if _truthy(row.get("active"), default=True)
        and clean_text(row.get("company_name"))
    }


def _looks_like_static_source(row: dict[str, Any]) -> bool:
    if not _truthy(row.get("active"), default=True):
        return False
    source_url = normalize_url(row.get("source_url"))
    if not source_url:
        return False
    material = " ".join(
        clean_text(row.get(field)).lower()
        for field in (
            "source_type",
            "ingestion_mode",
            "source_quality",
            "ats_platform",
            "source_url",
            "notes",
        )
    )
    if any(term in material for term in ("gmail_only", "manual_review_only", "disabled")):
        return False
    return any(
        term in material
        for term in (
            "static",
            "career",
            "job",
            "custom",
            "workday",
            "icims",
            "oracle",
            "ashby",
            "smartrecruiters",
        )
    )


def _zero_row(
    *,
    start: date,
    end: date,
    group_type: str,
    group_key: str,
    source_type: str,
    company: str = "",
    strategic_target: bool = False,
) -> SourceYieldRow:
    if strategic_target:
        recommendation = "keep_strategic_coverage"
        reason = "Retain strategic target-company coverage unless a validated replacement source is available."
    else:
        recommendation = "review_or_reduce_cadence"
        reason = "No leads were observed in the reporting window. Confirm the configuration still adds coverage before retiring it."
    return SourceYieldRow(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        group_type=group_type,
        group_key=group_key,
        source_type=source_type,
        company=company,
        strategic_target=strategic_target,
        leads_received=0,
        jobs_accepted=0,
        auto_rejected=0,
        blocked_company_rejects=0,
        too_junior_rejects=0,
        too_senior_rejects=0,
        surfaced_for_review=0,
        manually_dismissed=0,
        interested=0,
        applied=0,
        strong_fit_count=0,
        stretch_fit_count=0,
        average_potential_score=0.0,
        review_yield_percent=0.0,
        actionable_conversion_percent=0.0,
        recommendation=recommendation,
        recommendation_reason=reason,
    )


def configured_zero_yield_rows(
    *,
    company_rows: Iterable[dict[str, Any]],
    search_rows: Iterable[dict[str, Any]],
    target_companies: Iterable[dict[str, Any]],
    existing_rows: Iterable[SourceYieldRow],
    weeks: int = DEFAULT_WINDOW_WEEKS,
    as_of: date | None = None,
) -> list[SourceYieldRow]:
    end = as_of or datetime.now(UTC).date()
    start = end - timedelta(days=max(1, int(weeks or DEFAULT_WINDOW_WEEKS)) * 7 - 1)
    existing = {(row.group_type, row.group_key) for row in existing_rows}
    strategic = _strategic_companies(target_companies)
    output: list[SourceYieldRow] = []
    companies_added: set[str] = set()

    for row in company_rows:
        if not _looks_like_static_source(row):
            continue
        company = clean_text(row.get("company_name")) or "Unknown company"
        source_url = normalize_url(row.get("source_url"))
        group_key = f"{company} | {source_url or 'unknown URL'}"
        key = ("static_company_source", group_key)
        is_strategic = company.lower() in strategic
        source_type = _normalized(
            row.get("source_type")
            or row.get("ingestion_mode")
            or row.get("ats_platform")
            or "static_page"
        )
        if key not in existing:
            output.append(
                _zero_row(
                    start=start,
                    end=end,
                    group_type=key[0],
                    group_key=key[1],
                    source_type=source_type,
                    company=company,
                    strategic_target=is_strategic,
                )
            )
            existing.add(key)

        company_key = ("company", company)
        if company_key not in existing and company not in companies_added:
            output.append(
                _zero_row(
                    start=start,
                    end=end,
                    group_type="company",
                    group_key=company,
                    source_type=source_type,
                    company=company,
                    strategic_target=is_strategic,
                )
            )
            companies_added.add(company)
            existing.add(company_key)

    for row in search_rows:
        if not _truthy(row.get("active"), default=True):
            continue
        search_id = clean_text(row.get("search_id"))
        bucket = clean_text(row.get("bucket"))
        role_family = clean_text(row.get("role_family"))
        group_key = search_id or bucket or role_family
        if not group_key:
            continue
        key = ("gmail_alert_or_search", group_key)
        if key in existing:
            continue
        output.append(
            _zero_row(
                start=start,
                end=end,
                group_type=key[0],
                group_key=key[1],
                source_type="configured_search",
            )
        )
        existing.add(key)

    return sorted(output, key=lambda row: (row.group_type, row.group_key.lower()))


def run_source_quality_report(
    *,
    weeks: int = DEFAULT_WINDOW_WEEKS,
    probe_sources: bool = True,
    write_report: bool = False,
    approved_company_ids: set[str] | None = None,
    sheet_client: Any | None = None,
) -> dict[str, Any]:
    client = sheet_client or SheetClient.from_settings(load_settings())
    company_rows_with_numbers = client.read_records_with_row_numbers("Config_Companies")
    company_rows = [row for _, row in company_rows_with_numbers]
    search_rows = client.read_records("Config_Searches")
    runs = client.read_records("Runs")
    jobs = client.read_records("Jobs")
    job_sources = client.read_records("Job_Sources")
    rejected_jobs = client.read_records("Rejected_Jobs")
    target_companies = client.read_records("Target_Companies")

    findings = audit_static_sources(
        company_rows,
        runs=runs,
        probe_sources=probe_sources,
    )
    yield_rows = build_source_yield_report(
        jobs=jobs,
        job_sources=job_sources,
        rejected_jobs=rejected_jobs,
        target_companies=target_companies,
        weeks=weeks,
    )
    zero_rows = configured_zero_yield_rows(
        company_rows=company_rows,
        search_rows=search_rows,
        target_companies=target_companies,
        existing_rows=yield_rows,
        weeks=weeks,
    )
    yield_rows = sorted(
        [*yield_rows, *zero_rows],
        key=lambda row: (row.group_type, -row.leads_received, row.group_key.lower()),
    )

    updates: list[dict[str, Any]] = []
    if approved_company_ids:
        updates = apply_approved_source_updates(
            company_rows_with_numbers,
            findings,
            approved_company_ids=approved_company_ids,
            sheet_client=client,
        )

    writes = {
        "source_audit_rows_written": 0,
        "source_yield_rows_written": 0,
    }
    if write_report:
        writes = write_source_quality_surfaces(
            client,
            findings=findings,
            yield_rows=yield_rows,
        )
        client.append_run(
            build_run_record(
                findings=findings,
                yield_rows=yield_rows,
                updates=updates,
                weeks=weeks,
            )
        )

    classification_counts = Counter(finding.classification for finding in findings)
    recommendation_counts = Counter(row.recommendation for row in yield_rows)
    return {
        "status": "success",
        "weeks": weeks,
        "probe_sources": probe_sources,
        "sources_audited": len(findings),
        "classification_counts": {
            classification: classification_counts.get(classification, 0)
            for classification in sorted(AUDIT_CLASSIFICATIONS)
        },
        "configuration_changes_required": len(
            [finding for finding in findings if finding.requires_configuration_change]
        ),
        "retryable_sources": len(
            [finding for finding in findings if finding.retry_eligible]
        ),
        "yield_rows": len(yield_rows),
        "zero_result_rows": len(zero_rows),
        "yield_recommendation_counts": dict(sorted(recommendation_counts.items())),
        "approved_updates": updates,
        **writes,
        "findings": [finding.to_dict() for finding in findings],
        "source_yield": [row.to_dict() for row in yield_rows],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit source quality and generate a complete configurable source-yield report"
    )
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate output without writing generated sheets",
    )
    execution.add_argument(
        "--write-report",
        action="store_true",
        help="Write Source_Audit and Source_Yield generated sheets",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=DEFAULT_WINDOW_WEEKS,
        help="Reporting window in weeks",
    )
    parser.add_argument(
        "--skip-live-probes",
        action="store_true",
        help="Use configuration and workbook history without current HTTP probes",
    )
    parser.add_argument(
        "--approved-company-id",
        action="append",
        default=[],
        help="Exact company_id approved for a supported configuration update. May be repeated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    approved = {
        clean_text(value)
        for value in args.approved_company_id
        if clean_text(value)
    }
    if approved and not args.write_report:
        raise SystemExit(
            "Approved configuration updates require --write-report so the audit evidence is persisted."
        )
    result = run_source_quality_report(
        weeks=max(1, args.weeks),
        probe_sources=not args.skip_live_probes,
        write_report=args.write_report,
        approved_company_ids=approved,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
