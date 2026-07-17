from __future__ import annotations

import os
from pathlib import Path


def test_workflows_use_node24_compatible_action_versions() -> None:
    workflow_root = Path(os.environ.get("WORKFLOW_ROOT", ".github/workflows"))
    workflow_files = sorted(workflow_root.glob("*.yml"))
    assert workflow_files

    deprecated: list[str] = []
    for workflow in workflow_files:
        text = workflow.read_text(encoding="utf-8")
        if "actions/checkout@v4" in text:
            deprecated.append(f"{workflow}: actions/checkout@v4")
        if "actions/setup-python@v5" in text:
            deprecated.append(f"{workflow}: actions/setup-python@v5")

    assert not deprecated, "Deprecated Node 20 action references remain:\n" + "\n".join(deprecated)
