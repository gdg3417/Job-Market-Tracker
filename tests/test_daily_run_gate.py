from datetime import datetime, timezone

from src.daily_run_gate import daily_run_gate_decision


SUMMER_630_CENTRAL = datetime(2026, 6, 22, 11, 30, tzinfo=timezone.utc)
WINTER_530_CENTRAL = datetime(2026, 1, 12, 11, 30, tzinfo=timezone.utc)
WINTER_630_CENTRAL = datetime(2026, 1, 12, 12, 30, tzinfo=timezone.utc)
WINTER_730_CENTRAL = datetime(2026, 1, 12, 13, 30, tzinfo=timezone.utc)


def completion_record(status="success", finished_at="2026-06-22T12:00:00+00:00"):
    return {
        "run_type": "daily_workflow_completion",
        "status": status,
        "finished_at": finished_at,
    }


def test_first_scheduled_invocation_runs_without_completion():
    result = daily_run_gate_decision(event_name="schedule", run_records=[], now=SUMMER_630_CENTRAL)

    assert result["should_run"] is True
    assert result["gate_result"] == "scheduled_run_required"


def test_winter_530_central_invocation_skips_as_too_early():
    result = daily_run_gate_decision(event_name="schedule", run_records=[], now=WINTER_530_CENTRAL)

    assert result["should_run"] is False
    assert result["gate_result"] == "skipped_before_earliest_central_time"
    assert result["central_time"] == "05:30:00"
    assert result["scheduled_too_early"] is True


def test_winter_630_central_invocation_runs():
    result = daily_run_gate_decision(event_name="schedule", run_records=[], now=WINTER_630_CENTRAL)

    assert result["should_run"] is True
    assert result["gate_result"] == "scheduled_run_required"
    assert result["central_time"] == "06:30:00"


def test_delayed_scheduled_invocation_runs_after_earliest_time():
    result = daily_run_gate_decision(event_name="schedule", run_records=[], now=WINTER_730_CENTRAL)

    assert result["should_run"] is True
    assert result["gate_result"] == "scheduled_run_required"


def test_second_scheduled_invocation_skips_after_success():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record()],
        now=SUMMER_630_CENTRAL,
    )

    assert result["should_run"] is False
    assert result["gate_result"] == "skipped_already_completed"


def test_second_scheduled_invocation_runs_after_failed_first_attempt():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record(status="failed")],
        now=SUMMER_630_CENTRAL,
    )

    assert result["should_run"] is True


def test_manual_dispatch_bypasses_daily_lock_and_earliest_time():
    result = daily_run_gate_decision(
        event_name="workflow_dispatch",
        run_records=[completion_record(finished_at="2026-01-12T12:00:00+00:00")],
        now=WINTER_530_CENTRAL,
    )

    assert result["should_run"] is True
    assert result["gate_result"] == "manual_dispatch_allowed"


def test_prior_central_date_completion_does_not_block_today():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record(finished_at="2026-06-21T12:00:00+00:00")],
        now=SUMMER_630_CENTRAL,
    )

    assert result["should_run"] is True
