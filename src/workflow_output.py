from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_output(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load the final JSON object from command output that may contain log lines."""
    fallback = dict(default or {})
    output_path = Path(path)
    if not output_path.exists():
        return fallback

    text = output_path.read_text(encoding="utf-8").strip()
    if not text:
        return fallback

    decoder = json.JSONDecoder()
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        try:
            value, end_index = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if text[index + end_index :].strip():
            continue
        if isinstance(value, dict):
            return value
    return fallback
