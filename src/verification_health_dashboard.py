from __future__ import annotations

import json
from typing import Any

from src.verification_health_models import VerificationHealthResult

DASHBOARD_START = "Verification observability"
DASHBOARD_END = "End verification observability"


def _display_hours(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


def _display_rate(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def _job_rows(title: str, jobs: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = [[title], ["Company", "Title", "Age hours", "Blocker", "Detail", "URL"]]
    if not jobs:
        rows.append(["None", "", "", "", "", ""])
        return rows
    rows.extend([
        [job.get("company", ""), job.get("title", ""), job.get("age_hours", ""), job.get("blocker", ""), job.get("detail", ""), job.get("url", "")]
        for job in jobs
    ])
    return rows


def _mapping_rows(title: str, values: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [[title], ["Metric", "Value"]]
    if not values:
        rows.append(["None", 0])
        return rows
    rows.extend([[key, value] for key, value in values.items()])
    return rows


def build_dashboard_section(result: VerificationHealthResult) -> list[list[Any]]:
    rows: list[list[Any]] = [
        [DASHBOARD_START],
        ["Generated at", result.generated_at],
        ["Overall actionable verification health", result.overall_classification, result.overall_score],
        ["Overall reasons", "; ".join(result.overall_reasons)],
    ]
    if result.critical_overrides:
        rows.append(["Critical overrides", "; ".join(result.critical_overrides)])

    rows.extend([[""]])
    rows.extend(_mapping_rows("Actionable role summary", result.actionable_summary))
    rows.extend([[""]])
    rows.extend(_mapping_rows("Portfolio evidence coverage", result.portfolio_coverage))
    rows.extend([[""]])
    rows.extend(_mapping_rows("Roles excluded from actionable health", result.actionability_exclusions))

    rows.extend([[""], ["Health component scores"], ["Component", "Score", "Classification", "Critical", "Supporting metrics"]])
    for item in result.health_components:
        rows.append([
            item.label, item.score, item.classification,
            "TRUE" if item.critical else "FALSE",
            json.dumps(item.supporting_metrics, sort_keys=True),
        ])

    rows.extend([[""], ["Verification funnel and portfolio populations"], [
        "Stage", "Metric type", "Current", "Latest daily run", "Latest seven days", "Conversion",
        "Denominator", "Median age hours", "Oldest unresolved hours",
    ]])
    for item in result.funnel:
        rows.append([
            item.label, item.metric_type, item.current_count, item.latest_daily_count, item.latest_seven_day_count,
            _display_rate(item.conversion_rate), item.denominator_stage,
            _display_hours(item.median_age_hours), _display_hours(item.oldest_unresolved_age_hours),
        ])

    rows.extend([[""], ["Verification aging (actionable roles)"], [
        "Category", "Current", "Median age hours", "Oldest age hours", "Service level hours", "Breaches",
    ]])
    for item in result.aging:
        rows.append([
            item.label, item.current_count, _display_hours(item.median_age_hours),
            _display_hours(item.oldest_age_hours),
            item.service_level_hours if item.service_level_hours is not None else "",
            item.breach_count,
        ])

    rows.extend([[""], ["Service-level breaches (actionable roles)"], [
        "Company", "Title", "Age hours", "Service level hours", "Category", "Blocker", "URL",
    ]])
    if result.sla_breaches:
        for item in result.sla_breaches[: result.thresholds.dashboard_job_limit]:
            rows.append([
                item.get("company", ""), item.get("title", ""), item.get("age_hours", ""),
                item.get("service_level_hours", ""), item.get("category", ""),
                item.get("blocker", ""), item.get("url", ""),
            ])
    else:
        rows.append(["None", "", "", "", "", "", ""])

    rows.extend([[""]])
    rows.extend(_mapping_rows("Top blocker reasons (actionable primary blockers)", result.blocker_counts))
    rows.extend([[""]])
    rows.extend(_mapping_rows("Supporting secondary verification gaps", result.secondary_gap_counts))
    rows.extend([[""]])
    rows.extend(_mapping_rows("Primary blocker ownership", result.blocker_ownership_counts))
    rows.extend([[""]])
    rows.extend(_job_rows("Oldest unresolved high-potential jobs (actionable)", result.oldest_high_potential))
    rows.extend([[""]])
    rows.extend(_job_rows("Oldest unresolved target-company jobs (actionable)", result.oldest_target_company))
    rows.extend([[""]])
    rows.extend(_job_rows("Jobs requiring manual intervention (actionable)", result.manual_intervention))
    rows.append([DASHBOARD_END])
    return rows


def _remove_managed_section(values: list[list[Any]]) -> list[list[Any]]:
    output: list[list[Any]] = []
    in_section = False
    for row in values:
        first = str(row[0] if row else "").strip()
        if first == DASHBOARD_START:
            in_section = True
            continue
        if in_section and first == DASHBOARD_END:
            in_section = False
            continue
        if not in_section:
            output.append(row)
    while output and not any(str(value).strip() for value in output[-1]):
        output.pop()
    return output


def write_dashboard_section(sheet_client: Any, result: VerificationHealthResult) -> int:
    from src.sheets import with_quota_backoff

    worksheet = sheet_client.get_worksheet("Dashboard")
    existing = with_quota_backoff(
        lambda: worksheet.get_all_values(),
        operation_name="read Dashboard before verification health write",
    )
    section = build_dashboard_section(result)
    combined = _remove_managed_section(existing)
    if combined:
        combined.append([""])
    combined.extend(section)
    with_quota_backoff(lambda: worksheet.clear(), operation_name="clear Dashboard before verification health write")
    with_quota_backoff(
        lambda: worksheet.update(range_name="A1", values=combined, value_input_option="USER_ENTERED"),
        operation_name="write Dashboard verification health section",
    )
    return len(section)
