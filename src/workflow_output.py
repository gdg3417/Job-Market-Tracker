from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def load_json_output(
    path: str | Path,
    default: dict[str, Any] | None = None,
    *,
    required_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Load a JSON object from command output that may contain log lines."""
    fallback = dict(default or {})
    expected_keys = set(required_keys or [])
    output_path = Path(path)
    if not output_path.exists():
        return fallback

    text = output_path.read_text(encoding="utf-8").strip()
    if not text:
        return fallback

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end_index = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if expected_keys and not expected_keys.issubset(value):
            continue

        absolute_end_index = index + end_index
        if not text[absolute_end_index:].strip():
            return value
        candidates.append((index, absolute_end_index, value))

    if candidates:
        return max(candidates, key=lambda candidate: (candidate[1] - candidate[0], candidate[0]))[2]
    return fallback
