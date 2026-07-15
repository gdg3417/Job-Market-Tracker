from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/source-quality.yml")


def test_source_quality_argument_handoff_uses_bash_array_without_eval():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "printf '%s\\n' \"${args[@]}\"" in text
    assert 'mapfile -t source_quality_args < "$RUNNER_TEMP/source_quality_args.txt"' in text
    assert 'python -m src.source_quality_report "${source_quality_args[@]}"' in text
    assert 'eval "set --' not in text
    assert 'cat \\"$RUNNER_TEMP/source_quality_args.txt\\"' not in text
