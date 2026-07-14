import json
from datetime import datetime, timezone

from src.daily_run_gate import (
    DAILY_ATTEMPT_RUN_TYPE,
    build_daily_attempt_record,
    daily_run_gate_decision,
)


NOW = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)


def test_daily_attempt_record_captures_failed_gmail_outcome():
    record = build_daily_attempt_record(
        status="incomplete",
        gate_result="scheduled_run_required",
        gmail_status="all_new_messages_failed",
        gmail_failed=19,
        gmail_quarantined=0,
    )

    notes = json.loads(record["notes"])
    assert record["run_type"] == DAILY_ATTEMPT_RUN_TYPE
    assert record["status"] == "incomplete"
    assert record["records_failed"] == 19
    assert notes["gmail_status"] == "all_new_messages_failed"
    assert notes["successful_completion_recorded"] is False


def test_attempt_record_never_satisfies_successful_daily_completion_lock():
    attempt = build_daily_attempt_record(
        status="success",
        gmail_status="success",
        gmail_failed=0,
    )
    attempt["finished_at"] = "2026-07-14T12:00:00+00:00"

    decision = daily_run_gate_decision(
        event_name="schedule",
        run_records=[attempt],
        now=NOW,
    )

    assert decision["should_run"] is True
    assert decision["gate_result"] == "scheduled_run_required"
