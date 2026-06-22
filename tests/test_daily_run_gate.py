from datetime import datetime, timezone

from src.daily_run_gate import daily_run_gate_decision


NOW = datetime(2026, 6, 22, 12, 30, tzinfo=timezone.utc)


def completion_record(status="success", finished_at="2026-06-22T12:00:00+00:00"):
    return {
        "run_type": "daily_workflow_completion",
        "status": status,
        "finished_at": finished_at,
    }


def test_first_scheduled_invocation_runs_without_completion():
    result = daily_run_gate_decision(event_name="schedule", run_records=[], now=NOW)

    assert result["should_run"] is True
    assert result["gate_result"] == "scheduled_run_required"


def test_second_scheduled_invocation_skips_after_success():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record()],
        now=NOW,
    )

    assert result["should_run"] is False
    assert result["gate_result"] == "skipped_already_completed"


def test_second_scheduled_invocation_runs_after_failed_first_attempt():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record(status="failed")],
        now=NOW,
    )

    assert result["should_run"] is True


def test_manual_dispatch_bypasses_daily_lock():
    result = daily_run_gate_decision(
        event_name="workflow_dispatch",
        run_records=[completion_record()],
        now=NOW,
    )

    assert result["should_run"] is True
    assert result["gate_result"] == "manual_dispatch_allowed"


def test_prior_central_date_completion_does_not_block_today():
    result = daily_run_gate_decision(
        event_name="schedule",
        run_records=[completion_record(finished_at="2026-06-21T12:00:00+00:00")],
        now=NOW,
    )

    assert result["should_run"] is True
