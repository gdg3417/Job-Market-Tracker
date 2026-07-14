from __future__ import annotations

from datetime import date

import src.follow_up_core as _core
from src.follow_up_core import *
from src.follow_up_core import main
from src.models import parse_iso_date, today_iso
from src.sheet_dates import normalize_job

_core_evaluate_follow_up = _core.evaluate_follow_up


def evaluate_follow_up(job, *, as_of: str | date | None = None):
    """Evaluate follow-up state using normalized Google Sheets dates."""

    normalized_job = normalize_job(job)
    result = _core_evaluate_follow_up(normalized_job, as_of=as_of)
    if not result.outstanding_status_flag:
        return result

    as_of_date = parse_iso_date(as_of) or parse_iso_date(today_iso()) or date.today()
    explicit_dates = [
        parsed
        for value in (normalized_job.next_action_date, normalized_job.follow_up_date)
        if (parsed := parse_iso_date(value)) is not None
    ]
    due_dates = [value for value in explicit_dates if value <= as_of_date]
    if not due_dates:
        return result

    earliest_due = min(due_dates)
    return FollowUpEvaluation(
        outstanding_status=result.outstanding_status,
        last_status_update_date=result.last_status_update_date,
        days_since_status_update=result.days_since_status_update,
        follow_up_due=True,
        follow_up_reason=(
            f"Scheduled follow-up date {earliest_due.isoformat()} is due for "
            f"{result.outstanding_status}."
        ),
        outstanding_status_flag=True,
    )


_core.evaluate_follow_up = evaluate_follow_up


if __name__ == "__main__":
    main()
