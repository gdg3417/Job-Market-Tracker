from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(".github/workflows")
TEMP_WORKFLOW = WORKFLOWS / "_hotfix-self-apply.yml"
TEMP_SCRIPT = Path("scripts/_apply_hotfix.py")


def update_workflow_actions() -> list[str]:
    changed: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path == TEMP_WORKFLOW:
            continue
        original = path.read_text(encoding="utf-8")
        updated = original.replace("actions/checkout@v4", "actions/checkout@v6")
        updated = updated.replace("actions/setup-python@v5", "actions/setup-python@v6")
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(path.as_posix())
    return changed


def write_action_version_contract() -> None:
    path = Path("tests/test_workflow_action_versions.py")
    path.write_text(
        '''from __future__ import annotations

from pathlib import Path


def test_workflows_use_node24_compatible_action_versions() -> None:
    workflow_files = sorted(Path(".github/workflows").glob("*.yml"))
    assert workflow_files

    deprecated: list[str] = []
    for workflow in workflow_files:
        text = workflow.read_text(encoding="utf-8")
        if "actions/checkout@v4" in text:
            deprecated.append(f"{workflow}: actions/checkout@v4")
        if "actions/setup-python@v5" in text:
            deprecated.append(f"{workflow}: actions/setup-python@v5")

    assert not deprecated, "Deprecated Node 20 action references remain:\\n" + "\\n".join(deprecated)
''',
        encoding="utf-8",
    )


def add_stdout_regression_test() -> None:
    path = Path("tests/test_jobs_integrity_retry_scope.py")
    text = path.read_text(encoding="utf-8")
    marker = "def test_jobs_integrity_load_retry_notices_stay_off_stdout("
    if marker in text:
        return
    text += '''


def test_jobs_integrity_load_retry_notices_stay_off_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_backoff(operation, *, operation_name: str):
        assert operation_name == "load worksheet Jobs"
        print("Sheets API quota hit while loading Jobs")
        return operation()

    monkeypatch.setattr(sheets_module, "with_quota_backoff", fake_backoff)

    audit = audit_jobs_integrity(_QuotaAwareSheetClient())

    captured = capsys.readouterr()
    assert audit.healthy is True
    assert captured.out == ""
    assert "quota hit while loading Jobs" in captured.err
'''
    path.write_text(text, encoding="utf-8")


def remove_scaffolding() -> None:
    TEMP_WORKFLOW.unlink(missing_ok=True)
    TEMP_SCRIPT.unlink(missing_ok=True)


def main() -> None:
    changed = update_workflow_actions()
    write_action_version_contract()
    add_stdout_regression_test()
    remove_scaffolding()
    print(f"Updated {len(changed)} workflow files")
    for path in changed:
        print(path)


if __name__ == "__main__":
    main()
