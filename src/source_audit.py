from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from src.data_quality import JOB_BOARD_HOSTS, SEARCH_QUERY_KEYS
from src.models import utc_now_iso
from src.normalize import clean_text, normalize_url
from src.settings import load_settings
from src.sheets import SheetClient

SUCCESS = "success"
EMPTY = "empty"
FAILED = "failed"
TOO_NOISY = "too_noisy"
NEEDS_MANUAL_URL_CORRECTION = "needs_manual_url_correction"
DISABLE_RECOMMENDED = "disable_recommended"

GMAIL_ONLY = "gmail_only"
STATIC_DIRECT = "static_direct"
ATS_GREENHOUSE = "ats_greenhouse"
ATS_LEVER = "ats_lever"
MANUAL_REVIEW_ONLY = "manual_review_only"
DISABLED = "disabled"

SOURCE_QUALITY_VALUES = {
    SUCCESS,
    EMPTY,
    FAILED,
    TOO_NOISY,
    NEEDS_MANUAL_URL_CORRECTION,
    DISABLE_RECOMMENDED,
}
INGESTION_MODE_VALUES = {
    GMAIL_ONLY,
    STATIC_DIRECT,
    ATS_GREENHOUSE,
    ATS_LEVER,
    MANUAL_REVIEW_ONLY,
    DISABLED,
}

JOB_BOARD_MODE_RECOMMENDATIONS = {
    "builtin.com": (TOO_NOISY, GMAIL_ONLY, "Built In should not run as a generic static source. Use Gmail alerts or only direct posting URLs."),
    "builtindallas.com": (TOO_NOISY, GMAIL_ONLY, "Built In should not run as a generic static source. Use Gmail alerts or only direct posting URLs."),
    "builtinchicago.org": (TOO_NOISY, GMAIL_ONLY, "Built In should not run as a generic static source. Use Gmail alerts or only direct posting URLs."),
    "google.com": (TOO_NOISY, GMAIL_ONLY, "Google Jobs is a search surface, not a reliable static career page source."),
    "jobs.google.com": (TOO_NOISY, GMAIL_ONLY, "Google Jobs is a search surface, not a reliable static career page source."),
    "indeed.com": (TOO_NOISY, GMAIL_ONLY, "Indeed should enter through Gmail alerts or explicit APIs, not static scraping."),
    "linkedin.com": (TOO_NOISY, GMAIL_ONLY, "LinkedIn should enter through Gmail alerts or explicit direct posting links, not static company pages."),
    "theladders.com": (DISABLE_RECOMMENDED, DISABLED, "The Ladders search pages are too noisy for static ingestion."),
    "ladders.com": (DISABLE_RECOMMENDED, DISABLED, "The Ladders search pages are too noisy for static ingestion."),
    "simplyhired.com": (TOO_NOISY, GMAIL_ONLY, "SimplyHired should not run as a generic static source."),
    "ziprecruiter.com": (TOO_NOISY, GMAIL_ONLY, "ZipRecruiter should not run as a generic static source."),
}

KNOWN_SOURCE_ISSUES = {
    "fossil": (FAILED, MANUAL_REVIEW_ONLY, "Known static source returned 403. Use Gmail alerts or manually correct to a working ATS or direct company job source."),
    "fossil group": (FAILED, MANUAL_REVIEW_ONLY, "Known static source returned 403. Use Gmail alerts or manually correct to a working ATS or direct company job source."),
    "lennox": (NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "Known source URL had a DNS failure. Correct the URL before static ingestion."),
    "toyota financial services": (NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "Known source URL returned 404. Correct the URL before static ingestion."),
    "mary kay": (NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "Known source URL returned 404. Correct the URL before static ingestion."),
}

SEARCH_OR_NAVIGATION_PATH_TERMS = {
    "alert",
    "alerts",
    "browse",
    "categories",
    "category",
    "help",
    "job-alert",
    "job-alerts",
    "job-search",
    "jobsearch",
    "jobs-near-me",
    "landing",
    "near-me",
    "profile",
    "profiles",
    "resume",
    "resumes",
    "search",
    "services",
    "top-companies",
}

STATIC_SOURCE_URL_TERMS = (
    "career",
    "careers",
    "employment",
    "job",
    "jobs",
    "opening",
    "openings",
    "position",
    "positions",
    "recruit",
)
ATS_SOURCE_TERMS = (
    "greenhouse",
    "lever",
    "workday",
    "myworkdayjobs",
    "icims",
    "oraclecloud",
    "smartrecruiters",
    "ashby",
    "jobvite",
    "bamboohr",
)


@dataclass(frozen=True, slots=True)
class SourceAuditFinding:
    company_id: str
    company_name: str
    active: str
    source_type: str
    ats_platform: str
    source_url: str
    ingestion_mode_current: str
    source_quality_current: str
    recommended_ingestion_mode: str
    recommended_source_quality: str
    audit_status: str
    issue: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def _is_truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active", "x"}


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _host_for(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower().replace("www.", "")
    except ValueError:
        return ""


def _path_for(url: str) -> str:
    try:
        return urlsplit(url).path.lower()
    except ValueError:
        return ""


def _query_keys_for(url: str) -> set[str]:
    try:
        query = urlsplit(url).query
    except ValueError:
        return set()
    keys: set[str] = set()
    for part in query.split("&"):
        if part:
            keys.add(part.split("=", 1)[0])
    return keys


def _path_parts(path: str) -> set[str]:
    return {part for part in re.split(r"[/-]+", path.lower()) if part}


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(clean_text(row.get(field_name, "")).lower() for field_name in ["source_type", "ats_platform", "source_url", "source_slug", "ingestion_mode", "notes"])


def normalize_ingestion_mode(value: Any) -> str:
    text = clean_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "greenhouse": ATS_GREENHOUSE,
        "ats_greenhouse": ATS_GREENHOUSE,
        "lever": ATS_LEVER,
        "ats_lever": ATS_LEVER,
        "static": STATIC_DIRECT,
        "static_page": STATIC_DIRECT,
        "career_page": STATIC_DIRECT,
        "careers_page": STATIC_DIRECT,
        "company_career": STATIC_DIRECT,
        "gmail": GMAIL_ONLY,
        "gmail_alert": GMAIL_ONLY,
        "gmail_alerts": GMAIL_ONLY,
        "manual": MANUAL_REVIEW_ONLY,
        "manual_review": MANUAL_REVIEW_ONLY,
        "manual_review_only": MANUAL_REVIEW_ONLY,
        "off": DISABLED,
        "inactive": DISABLED,
        "disabled": DISABLED,
    }
    return aliases.get(text, text if text in INGESTION_MODE_VALUES else "")


def _current_ingestion_mode(row: dict[str, Any]) -> str:
    explicit = normalize_ingestion_mode(row.get("ingestion_mode"))
    if explicit:
        return explicit
    for field_name in ["source_type", "ats_platform"]:
        inferred = normalize_ingestion_mode(row.get(field_name))
        if inferred:
            return inferred
    return ""


def _current_source_quality(row: dict[str, Any]) -> str:
    text = clean_text(row.get("source_quality")).lower().replace("-", "_").replace(" ", "_")
    return text if text in SOURCE_QUALITY_VALUES else ""


def _known_issue_for(company_name: str, row_text: str) -> tuple[str, str, str] | None:
    company_identity = _identity(company_name)
    issue = KNOWN_SOURCE_ISSUES.get(company_identity)
    if not issue:
        for key, value in KNOWN_SOURCE_ISSUES.items():
            if key in company_identity:
                issue = value
                break
    if not issue:
        return None
    if "greenhouse" in row_text or "lever" in row_text or GMAIL_ONLY in row_text or DISABLED in row_text:
        return None
    return issue


def _job_board_recommendation(host: str) -> tuple[str, str, str] | None:
    for suffix, recommendation in JOB_BOARD_MODE_RECOMMENDATIONS.items():
        if _host_matches(host, suffix):
            return recommendation
    if any(_host_matches(host, suffix) for suffix in JOB_BOARD_HOSTS):
        return TOO_NOISY, GMAIL_ONLY, "Job board sources should not run as generic static career pages."
    return None


def _is_search_or_navigation_url(url: str) -> bool:
    path = _path_for(url)
    path_parts = _path_parts(path)
    query_keys = _query_keys_for(url)
    if path_parts & SEARCH_OR_NAVIGATION_PATH_TERMS:
        return True
    if query_keys & SEARCH_QUERY_KEYS:
        return True
    return False


def _looks_like_static_company_source(url: str, row_text: str) -> bool:
    lowered_url = url.lower()
    if any(term in lowered_url for term in STATIC_SOURCE_URL_TERMS):
        return True
    if any(term in row_text for term in ["static", "career_page", "careers_page", "company_career", "custom"]):
        return True
    if any(term in row_text for term in ATS_SOURCE_TERMS):
        return True
    return False


def _ats_recommendation(row_text: str, url: str) -> tuple[str, str, str] | None:
    combined = f"{row_text} {url.lower()}"
    if "greenhouse" in combined:
        return SUCCESS, ATS_GREENHOUSE, "Use Greenhouse ingestion rather than static HTML parsing."
    if "lever" in combined:
        return SUCCESS, ATS_LEVER, "Use Lever ingestion rather than static HTML parsing."
    return None


def classify_source_row(row: dict[str, Any]) -> SourceAuditFinding:
    company_name = clean_text(row.get("company_name"))
    source_url = normalize_url(row.get("source_url", ""))
    source_type = clean_text(row.get("source_type"))
    ats_platform = clean_text(row.get("ats_platform"))
    active = "TRUE" if _is_truthy(row.get("active"), default=True) else "FALSE"
    current_mode = _current_ingestion_mode(row)
    current_quality = _current_source_quality(row)
    row_text = _row_text(row)

    def finding(status: str, mode: str, issue: str, action: str | None = None) -> SourceAuditFinding:
        return SourceAuditFinding(
            company_id=clean_text(row.get("company_id")),
            company_name=company_name,
            active=active,
            source_type=source_type,
            ats_platform=ats_platform,
            source_url=source_url,
            ingestion_mode_current=current_mode,
            source_quality_current=current_quality,
            recommended_ingestion_mode=mode,
            recommended_source_quality=status,
            audit_status=status,
            issue=issue,
            suggested_action=action or issue,
        )

    if current_mode == DISABLED or active == "FALSE":
        return finding(DISABLE_RECOMMENDED, DISABLED, "Source is already inactive or explicitly disabled.", "Leave disabled unless there is a corrected source URL.")

    if not source_url:
        return finding(NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "Missing source_url.", "Add a direct ATS, company career page, Gmail-only mode, or disable the row.")

    known_issue = _known_issue_for(company_name, row_text)
    if known_issue:
        status, mode, issue = known_issue
        return finding(status, mode, issue)

    host = _host_for(source_url)
    if not host:
        return finding(NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "source_url is not a valid HTTP URL.", "Correct the URL or disable the row.")

    ats_recommendation = _ats_recommendation(row_text, source_url)
    if ats_recommendation:
        status, mode, issue = ats_recommendation
        return finding(status, mode, issue)

    board_recommendation = _job_board_recommendation(host)
    if board_recommendation:
        status, mode, issue = board_recommendation
        return finding(status, mode, issue)

    if _is_search_or_navigation_url(source_url):
        return finding(NEEDS_MANUAL_URL_CORRECTION, MANUAL_REVIEW_ONLY, "URL is a search, category, alert, near-me, profile, resume, services, or help path.", "Replace with a direct company career page or disable the row.")

    if _looks_like_static_company_source(source_url, row_text):
        return finding(SUCCESS, STATIC_DIRECT, "Source is eligible for static direct company career page ingestion.", "Keep enabled for static ingestion.")

    return finding(EMPTY, MANUAL_REVIEW_ONLY, "Source does not look like a reliable ATS, Gmail-only, or static company career page.", "Review manually before using this source for unattended ingestion.")


def audit_source_configuration(company_rows: list[dict[str, Any]]) -> list[SourceAuditFinding]:
    return [classify_source_row(row) for row in company_rows]


def summarize_source_audit(findings: list[SourceAuditFinding]) -> dict[str, Any]:
    status_counts: dict[str, int] = {status: 0 for status in [SUCCESS, EMPTY, FAILED, TOO_NOISY, NEEDS_MANUAL_URL_CORRECTION, DISABLE_RECOMMENDED]}
    mode_counts: dict[str, int] = {mode: 0 for mode in sorted(INGESTION_MODE_VALUES)}
    for finding in findings:
        status_counts[finding.audit_status] = status_counts.get(finding.audit_status, 0) + 1
        mode_counts[finding.recommended_ingestion_mode] = mode_counts.get(finding.recommended_ingestion_mode, 0) + 1
    issue_count = len([finding for finding in findings if finding.audit_status != SUCCESS])
    return {
        "sources_audited": len(findings),
        "issue_count": issue_count,
        "status_counts": status_counts,
        "recommended_ingestion_mode_counts": mode_counts,
    }


def source_audit_record_updates(row: dict[str, Any], finding: SourceAuditFinding) -> dict[str, Any]:
    updated = dict(row)
    updated["source_quality"] = finding.recommended_source_quality
    updated["ingestion_mode"] = finding.recommended_ingestion_mode
    if finding.recommended_ingestion_mode in {DISABLED, MANUAL_REVIEW_ONLY}:
        updated["active"] = "FALSE"
    existing_notes = clean_text(updated.get("notes"))
    marker = f"Sprint 18 source audit: {finding.audit_status}: {finding.issue}"
    if marker not in existing_notes:
        updated["notes"] = f"{existing_notes} | {marker}" if existing_notes else marker
    return updated


def build_sprint18_run_record(summary: dict[str, Any], *, updates_applied: int = 0) -> dict[str, Any]:
    now = utc_now_iso()
    run_timestamp = now.replace(":", "").replace("-", "").replace("+0000", "Z").replace("+00:00", "Z")
    issue_count = int(summary.get("issue_count") or 0)
    return {
        "run_id": f"sprint18_source_audit_{run_timestamp}",
        "run_type": "sprint_18_source_configuration_audit",
        "source_type": "config_companies",
        "source_name": "Config_Companies source audit",
        "status": "success" if issue_count == 0 else "source_cleanup_needed",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0,
        "records_found": summary.get("sources_audited", 0),
        "records_inserted": 0,
        "records_updated": updates_applied,
        "records_failed": issue_count,
        "rows_read": summary.get("sources_audited", 0),
        "config_companies_rows": summary.get("sources_audited", 0),
        "config_searches_rows": 0,
        "companies_read": summary.get("sources_audited", 0),
        "searches_read": 0,
        "error_message": "",
        "notes": json.dumps(summary, sort_keys=True),
        "created_at": now,
        "updated_at": now,
    }


def run_source_audit_report(*, apply_recommendations: bool = False) -> dict[str, Any]:
    settings = load_settings()
    sheet_client = SheetClient.from_settings(settings)
    rows_with_numbers = sheet_client.read_records_with_row_numbers("Config_Companies")
    rows = [row for _, row in rows_with_numbers]
    findings = audit_source_configuration(rows)
    summary = summarize_source_audit(findings)
    updates_applied = 0

    if apply_recommendations:
        for row_number, row in rows_with_numbers:
            finding = classify_source_row(row)
            updated = source_audit_record_updates(row, finding)
            if updated != row:
                sheet_client.update_record("Config_Companies", row_number, updated)
                updates_applied += 1

    sheet_client.append_run(build_sprint18_run_record(summary, updates_applied=updates_applied))

    return {
        "run_mode": "sprint_18_source_configuration_audit",
        "status": "success" if summary["issue_count"] == 0 else "source_cleanup_needed",
        "updates_applied": updates_applied,
        **summary,
        "findings": [finding.to_dict() for finding in findings],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Job Market Tracker source configuration")
    parser.add_argument("--apply-recommendations", action="store_true", help="Update Config_Companies with recommended source_quality and ingestion_mode values")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_source_audit_report(apply_recommendations=args.apply_recommendations), indent=2))


if __name__ == "__main__":
    main()
