from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-run.yml"


def test_daily_workflow_exposes_bounded_replay_modes():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "replay_mode:" in workflow
    assert "- failed_only" in workflow
    assert "- selected" in workflow
    assert "force_reprocess_selected:" in workflow
    assert "--retry-failed-only" in workflow
    assert "--force-reprocess-selected" in workflow
    assert "--max-message-attempts" in workflow


def test_selected_replay_requires_exact_safe_message_identifiers():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Selected Gmail replay requires at least one exact message ID" in workflow
    assert "^[A-Za-z0-9_-]+$" in workflow
    assert "Invalid Gmail message ID" in workflow


def test_broad_force_reprocessing_is_not_exposed_as_a_workflow_input():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "      force_reprocess:\n" not in workflow
    assert "force_reprocess_selected:" in workflow
