from __future__ import annotations

from pathlib import Path


def update_jobs_integrity() -> None:
    path = Path("src/jobs_integrity.py")
    text = path.read_text(encoding="utf-8")
    old = "    return SheetClient.from_settings(load_settings())\n"
    new = (
        "    with contextlib.redirect_stdout(sys.stderr):\n"
        "        return SheetClient.from_settings(load_settings())\n"
    )
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError("Expected exactly one SheetClient.from_settings return in jobs_integrity.py")
    path.write_text(text.replace(old, new), encoding="utf-8")


def update_retry_scope_tests() -> None:
    path = Path("tests/test_jobs_integrity_retry_scope.py")
    text = path.read_text(encoding="utf-8")
    import_line = "import src.jobs_integrity as jobs_integrity_module\n"
    if import_line not in text:
        text = text.replace(
            "import pytest\n\nimport src.sheets as sheets_module\n",
            "import pytest\n\nimport src.jobs_integrity as jobs_integrity_module\nimport src.sheets as sheets_module\n",
        )

    marker = "def test_jobs_integrity_client_load_retry_notices_stay_off_stdout("
    if marker not in text:
        text = text.rstrip() + '''\n\n\ndef test_jobs_integrity_client_load_retry_notices_stay_off_stdout(\n    monkeypatch: pytest.MonkeyPatch,\n    capsys: pytest.CaptureFixture[str],\n) -> None:\n    sentinel = _SheetClient()\n\n    monkeypatch.setattr("src.settings.load_settings", lambda: object())\n\n    def fake_from_settings(settings: object) -> _SheetClient:\n        assert settings is not None\n        print("Sheets API quota hit while opening workbook")\n        return sentinel\n\n    monkeypatch.setattr(\n        sheets_module.SheetClient,\n        "from_settings",\n        staticmethod(fake_from_settings),\n    )\n\n    assert jobs_integrity_module._load_sheet_client() is sentinel\n\n    captured = capsys.readouterr()\n    assert captured.out == ""\n    assert "quota hit while opening workbook" in captured.err\n'''
    path.write_text(text, encoding="utf-8")


def update_workflow_contract_test() -> None:
    path = Path("tests/test_workflow_action_versions.py")
    path.write_text(
        '''from __future__ import annotations\n\nfrom pathlib import Path\n\n\ndef test_workflows_use_node24_compatible_action_versions() -> None:\n    workflow_root = Path(".github/workflows")\n    workflow_files = sorted(\n        [\n            *workflow_root.glob("*.yml"),\n            *workflow_root.glob("*.yaml"),\n        ]\n    )\n    assert workflow_files\n\n    deprecated: list[str] = []\n    for workflow in workflow_files:\n        text = workflow.read_text(encoding="utf-8")\n        if "actions/checkout@v4" in text:\n            deprecated.append(f"{workflow}: actions/checkout@v4")\n        if "actions/setup-python@v5" in text:\n            deprecated.append(f"{workflow}: actions/setup-python@v5")\n\n    assert not deprecated, "Deprecated Node 20 action references remain:\\n" + "\\n".join(deprecated)\n''',
        encoding="utf-8",
    )


def main() -> None:
    update_jobs_integrity()
    update_retry_scope_tests()
    update_workflow_contract_test()


if __name__ == "__main__":
    main()
