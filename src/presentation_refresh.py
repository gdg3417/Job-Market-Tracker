from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import src.dashboard as dashboard
from src.follow_up import apply_follow_up_queue
from src.generated_surface_policy import (
    include_in_current_context,
    include_in_dashboard,
    include_on_follow_up_queue,
    include_on_review_queue,
)
from src.models import JobPosting, today_iso, utc_now_iso
from src.review_queue import apply_review_queue
from src.settings import load_settings
from src.sheet_dates import (
    REJECTED_DATE_FIELDS,
    normalize_jobs_with_rows,
    normalize_record_dates,
    normalize_sheet_date,
)
from src.sheet_governance import apply_sheet_governance
from src.sheets import SheetClient
from src.surface_status import SurfaceOutcome, write_surface_status
from src.weekly_context import load_weekly_digest_config
from src.weekly_context_hotfix import apply_weekly_context
from src.weekly_value import WEEKLY_VALUE_SHEET, apply_weekly_value


@dataclass(slots=True)
class CanonicalSnapshot:
    jobs_with_rows: list[tuple[int, JobPosting]]
    rejected_rows: list[dict[str, Any]]
    target_company_rows: list[dict[str, Any]]
    config_company_rows: list[dict[str, Any]]
    runs_rows: list[dict[str, Any]]
    weekly_value_rows: list[dict[str, Any]]

    @property
    def jobs(self) -> list[JobPosting]:
        return [job for _, job in self.jobs_with_rows]


class SnapshotSheetClient:
    """Delegate writes while returning one canonical Jobs snapshot to readers."""

    def __init__(
        self,
        sheet_client: SheetClient,
        jobs_with_rows: list[tuple[int, JobPosting]],
    ) -> None:
        self._sheet_client = sheet_client
        self._jobs_with_rows = list(jobs_with_rows)

    def read_jobs_with_row_numbers(self) -> list[tuple[int, JobPosting]]:
        return list(self._jobs_with_rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sheet_client, name)


def _read_optional_records(
    sheet_client: SheetClient,
    worksheet_name: str,
) -> list[dict[str, Any]]:
    try:
        return sheet_client.read_records(worksheet_name)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def read_canonical_snapshot(sheet_client: SheetClient) -> CanonicalSnapshot:
    raw_jobs = sheet_client.read_records("Jobs")
    return CanonicalSnapshot(
        jobs_with_rows=normalize_jobs_with_rows(raw_jobs),
        rejected_rows=[
            normalize_record_dates(record, REJECTED_DATE_FIELDS)
            for record in _read_optional_records(sheet_client, "Rejected_Jobs")
        ],
        target_company_rows=_read_optional_records(sheet_client, "Target_Companies"),
        config_company_rows=_read_optional_records(sheet_client, "Config_Companies"),
        runs_rows=_read_optional_records(sheet_client, "Runs"),
        weekly_value_rows=_read_optional_records(sheet_client, WEEKLY_VALUE_SHEET),
    )


def review_queue_snapshot_rows(
    jobs_with_rows: list[tuple[int, JobPosting]],
) -> list[tuple[int, JobPosting]]:
    """Suppress queue candidates without shortening the canonical Jobs filter range.

    The existing Review Queue writer also reapplies the filter and freeze settings
    on `Jobs` using the number of supplied rows. Excluded rows are therefore
    represented by identity-free placeholders rather than removed from the list.
    The queue builder ignores those placeholders while the `Jobs` filter retains
    the full canonical row extent.
    """

    return [
        (row_number, job if include_on_review_queue(job) else JobPosting())
        for row_number, job in jobs_with_rows
    ]


def _safe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return f"{exc.__class__.__name__}: {text}"[:500]


def _warnings(payload: dict[str, Any]) -> str:
    values = payload.get("warnings") or []
    if isinstance(values, str):
        return values[:500]
    return "; ".join(str(value) for value in values if str(value).strip())[:500]


def _rows_written(payload: dict[str, Any]) -> int:
    for key in (
        "rows_written",
        "review_queue_rows_written",
        "dashboard_rows_written",
        "digest_rows_written",
        "guide_rows_written",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _run_surface(
    name: str,
    action: Callable[[], dict[str, Any]],
) -> tuple[SurfaceOutcome, dict[str, Any]]:
    try:
        payload = dict(action())
        payload.setdefault("status", "success")
        return (
            SurfaceOutcome(
                surface_name=name,
                status="success",
                rows_written=_rows_written(payload),
                warning_or_error=_warnings(payload),
            ),
            payload,
        )
    except Exception as exc:
        error = _safe_error(exc)
        return (
            SurfaceOutcome(
                surface_name=name,
                status="failed",
                warning_or_error=error,
            ),
            {"status": "failed", "error": error},
        )


def _dashboard_result(
    jobs: list[JobPosting],
    digest_values: list[list[Any]],
    dashboard_values: list[list[Any]],
) -> dashboard.DashboardDigestResult:
    return dashboard.DashboardDigestResult(
        jobs_read=len(jobs),
        open_jobs=sum(1 for job in jobs if dashboard._is_open(job)),
        digest_rows=max(0, len(digest_values) - 5),
        immediate_review_rows=dashboard._count_digest_section(
            digest_values, "Immediate review"
        ),
        strong_fit_rows=dashboard._count_digest_section(digest_values, "Strong fit"),
        verified_strong_fit_rows=dashboard._count_digest_section(
            digest_values, "Verified strong fits"
        ),
        high_potential_pending_rows=dashboard._count_digest_section(
            digest_values, "High-potential roles awaiting enrichment"
        ),
        high_potential_partial_rows=dashboard._count_digest_section(
            digest_values, "High-potential roles with partial evidence"
        ),
        enrichment_failure_rows=dashboard._count_digest_section(
            digest_values, "Enrichment failures requiring review"
        ),
        high_signal_review_rows=dashboard._count_digest_section(
            digest_values, "High-signal titles needing review"
        ),
        target_company_watchlist_rows=dashboard._count_digest_section(
            digest_values, "Target company watchlist"
        ),
        needs_salary_research_rows=dashboard._count_digest_section(
            digest_values, "Needs salary research"
        ),
        remote_or_short_commute_rows=dashboard._count_digest_section(
            digest_values, "Remote or short commute"
        ),
        pnl_pathway_rows=dashboard._count_digest_section(digest_values, "P&L pathway"),
        rejected_source_audit_rows=dashboard._count_digest_section(
            digest_values, "Rejected source audit"
        ),
        dashboard_rows_written=len(dashboard_values),
        digest_rows_written=len(digest_values),
    )


def _add_warning(
    outcomes: list[SurfaceOutcome],
    results: dict[str, dict[str, Any]],
    surface_names: set[str],
    warning: str,
) -> None:
    for outcome in outcomes:
        if outcome.surface_name not in surface_names:
            continue
        outcome.warning_or_error = "; ".join(
            value for value in (outcome.warning_or_error, warning) if value
        )[:500]
        payload = results.get(outcome.surface_name)
        if payload is not None:
            warnings = payload.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(warning)


def apply_presentation_refresh(
    sheet_client: SheetClient,
    *,
    as_of: str | date | None = None,
    backfill_weeks: int = 12,
    source_run: str = "manual",
    apply_governance: bool = False,
    config_path: str | None = None,
) -> dict[str, Any]:
    attempted_at = utc_now_iso()
    normalized_as_of = (
        normalize_sheet_date(as_of) if as_of not in (None, "") else today_iso()
    )
    data_as_of_date = str(normalized_as_of or today_iso())
    snapshot = read_canonical_snapshot(sheet_client)
    results: dict[str, dict[str, Any]] = {}
    outcomes: list[SurfaceOutcome] = []

    review_client = SnapshotSheetClient(
        sheet_client,
        review_queue_snapshot_rows(snapshot.jobs_with_rows),
    )
    outcome, payload = _run_surface(
        "Review_Queue",
        lambda: apply_review_queue(review_client).to_dict(),
    )
    outcomes.append(outcome)
    results["Review_Queue"] = payload

    follow_up_jobs_with_rows = [
        pair for pair in snapshot.jobs_with_rows if include_on_follow_up_queue(pair[1])
    ]
    follow_up_client = SnapshotSheetClient(sheet_client, follow_up_jobs_with_rows)
    outcome, payload = _run_surface(
        "Follow_Up_Queue",
        lambda: apply_follow_up_queue(
            follow_up_client,
            as_of=data_as_of_date,
        ).to_dict(),
    )
    outcomes.append(outcome)
    results["Follow_Up_Queue"] = payload

    outcome, payload = _run_surface(
        "Weekly_Value",
        lambda: apply_weekly_value(
            sheet_client,
            as_of=data_as_of_date,
            backfill_weeks=backfill_weeks,
            jobs=snapshot.jobs,
            rejected_job_rows=snapshot.rejected_rows,
        ).to_dict(),
    )
    outcomes.append(outcome)
    results["Weekly_Value"] = payload

    weekly_records = snapshot.weekly_value_rows
    weekly_value_readback_warning = ""
    if outcome.status == "success":
        try:
            weekly_records = _read_optional_records(sheet_client, WEEKLY_VALUE_SHEET)
        except Exception as exc:
            weekly_value_readback_warning = (
                "Weekly_Value readback failed; Weekly_Context used the prior snapshot: "
                f"{_safe_error(exc)}"
            )
            _add_warning(
                outcomes,
                results,
                {"Weekly_Value"},
                weekly_value_readback_warning,
            )

    context_jobs_with_rows = [
        pair for pair in snapshot.jobs_with_rows if include_in_current_context(pair[1])
    ]

    def refresh_context() -> dict[str, Any]:
        context_result = apply_weekly_context(
            sheet_client,
            as_of=data_as_of_date,
            jobs_with_rows=context_jobs_with_rows,
            weekly_records=weekly_records,
            config=load_weekly_digest_config(config_path),
        ).to_dict()
        if results["Weekly_Value"].get("status") == "failed":
            context_result.setdefault("warnings", []).append(
                "Weekly_Context used the prior Weekly_Value snapshot because the current refresh failed."
            )
        elif weekly_value_readback_warning:
            context_result.setdefault("warnings", []).append(
                weekly_value_readback_warning
            )
        return context_result

    outcome, payload = _run_surface("Weekly_Context", refresh_context)
    outcomes.append(outcome)
    results["Weekly_Context"] = payload

    dashboard_jobs = [job for job in snapshot.jobs if include_in_dashboard(job)]
    digest_build_error = ""
    digest_values: list[list[Any]] = []
    dashboard_values: list[list[Any]] = []
    try:
        digest_values = dashboard.build_digest_values(
            dashboard_jobs,
            as_of=data_as_of_date,
            target_company_rows=snapshot.target_company_rows,
            config_company_rows=snapshot.config_company_rows,
            rejected_job_rows=snapshot.rejected_rows,
        )
        dashboard_values = dashboard.build_dashboard_values(
            dashboard_jobs,
            digest_rows=digest_values[5:],
            target_company_rows=snapshot.target_company_rows,
            config_company_rows=snapshot.config_company_rows,
            rejected_job_rows=snapshot.rejected_rows,
            runs_rows=snapshot.runs_rows,
            generated_at=attempted_at,
        )
    except Exception as exc:
        digest_build_error = _safe_error(exc)

    def refresh_dashboard() -> dict[str, Any]:
        if digest_build_error:
            raise RuntimeError(digest_build_error)
        dashboard.write_values(sheet_client, "Dashboard", dashboard_values)
        return {
            "status": "success",
            "dashboard_rows_written": len(dashboard_values),
            "warnings": [],
        }

    outcome, payload = _run_surface("Dashboard", refresh_dashboard)
    outcomes.append(outcome)
    results["Dashboard"] = payload

    def refresh_digest() -> dict[str, Any]:
        if digest_build_error:
            raise RuntimeError(digest_build_error)
        dashboard.write_values(sheet_client, "Digest", digest_values)
        return {
            "status": "success",
            "digest_rows_written": len(digest_values),
            "digest_rows": max(0, len(digest_values) - 5),
            "warnings": [],
        }

    outcome, payload = _run_surface("Digest", refresh_digest)
    outcomes.append(outcome)
    results["Digest"] = payload

    if (
        results["Dashboard"].get("status") == "success"
        and results["Digest"].get("status") == "success"
    ):
        try:
            combined = _dashboard_result(
                dashboard_jobs,
                digest_values,
                dashboard_values,
            )
            sheet_client.append_run(dashboard.build_dashboard_run_record(combined))
        except Exception as exc:
            _add_warning(
                outcomes,
                results,
                {"Dashboard", "Digest"},
                f"Dashboard run history was not recorded: {_safe_error(exc)}",
            )

    if apply_governance:
        outcome, payload = _run_surface(
            "Governance",
            lambda: apply_sheet_governance(sheet_client).to_dict(),
        )
        outcomes.append(outcome)
        results["Governance"] = payload

    surface_status_error = ""
    try:
        merged_outcomes = write_surface_status(
            sheet_client,
            outcomes,
            source_run=source_run,
            data_as_of_date=data_as_of_date,
            attempted_at=attempted_at,
        )
    except Exception as exc:
        surface_status_error = _safe_error(exc)
        merged_outcomes = [
            *outcomes,
            SurfaceOutcome(
                surface_name="Surface_Status",
                status="failed",
                warning_or_error=surface_status_error,
                source_run=source_run,
                data_as_of_date=data_as_of_date,
                last_attempted_at=attempted_at,
            ),
        ]

    failures = [item for item in merged_outcomes if item.status != "success"]
    warnings = [
        item
        for item in merged_outcomes
        if item.status == "success" and item.warning_or_error
    ]
    overall_status = "success" if not failures else "partial_failure"

    return {
        "run_mode": "sprint_49_generated_surface_refresh",
        "status": overall_status,
        "source_run": source_run,
        "data_as_of_date": data_as_of_date,
        "jobs_snapshot_rows": len(snapshot.jobs_with_rows),
        "surface_order": [item.surface_name for item in merged_outcomes],
        "surfaces_succeeded": sum(
            1 for item in merged_outcomes if item.status == "success"
        ),
        "surfaces_failed": len(failures),
        "surfaces_with_warnings": len(warnings),
        "surface_status_written": not bool(surface_status_error),
        "surface_status_error": surface_status_error,
        "dashboard_rows_written": int(
            results.get("Dashboard", {}).get("dashboard_rows_written") or 0
        ),
        "digest_rows_written": int(
            results.get("Digest", {}).get("digest_rows_written") or 0
        ),
        "results": results,
        "surface_status": [item.to_dict() for item in merged_outcomes],
        "generated_at": attempted_at,
    }


def run_presentation_refresh(
    *,
    as_of: str | None = None,
    backfill_weeks: int = 12,
    source_run: str = "manual",
    apply_governance: bool = False,
    config_path: str | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    return apply_presentation_refresh(
        SheetClient.from_settings(settings),
        as_of=as_of,
        backfill_weeks=backfill_weeks,
        source_run=source_run,
        apply_governance=apply_governance,
        config_path=config_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh all generated Job Market Tracker surfaces from one canonical snapshot"
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--backfill-weeks", type=int, default=12)
    parser.add_argument("--source-run", default="manual")
    parser.add_argument("--governance", action="store_true")
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_presentation_refresh(
        as_of=args.as_of,
        backfill_weeks=max(1, args.backfill_weeks),
        source_run=args.source_run,
        apply_governance=args.governance,
        config_path=args.config,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
