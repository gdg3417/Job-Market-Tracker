from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "apps_script" / "weekly_digest_email.gs"


def test_weekly_email_supports_high_signal_review_section():
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    section = "High-signal titles needing review"
    assert f"'{section}': 15" in script
    assert script.count(f"'{section}'") >= 5
    assert "counts['High-signal titles needing review']" in script
    assert "sparse high-signal roles need review" in script


def test_weekly_email_action_order_places_review_after_strong_fit():
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    action_start = script.index("const ACTION_SECTIONS")
    action_end = script.index("];", action_start)
    action_block = script[action_start:action_end]
    assert action_block.index("'Strong fit'") < action_block.index("'High-signal titles needing review'")
    assert action_block.index("'High-signal titles needing review'") < action_block.index("'Needs salary research'")


def test_weekly_email_actionable_count_includes_high_signal_review_roles():
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    actionable_line = next(line for line in script.splitlines() if "const actionableCount" in line)
    assert "counts['High-signal titles needing review']" in actionable_line
