from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_GMAIL_MAX_RESULTS = 50
MAX_GMAIL_MAX_RESULTS = 500
# Backward-compatible alias for callers that still import the Sprint 9 name.
DEFAULT_GMAIL_SMOKE_MAX_RESULTS = DEFAULT_GMAIL_MAX_RESULTS


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    config_dir: Path = CONFIG_DIR
    scoring_rules_path: Path = CONFIG_DIR / "scoring_rules.yml"
    target_profile_path: Path = CONFIG_DIR / "target_profile.yml"
    google_sheet_id: str = ""
    google_application_credentials: str = ""
    gmail_client_config: str = ""
    gmail_token_json: str = ""
    gmail_label_name: str = "Job Tracker"
    gmail_max_results: int = DEFAULT_GMAIL_MAX_RESULTS
    dry_run: bool = True


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _resolve_project_path(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    credentials_path = _resolve_project_path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    gmail_client_config = _resolve_project_path(os.getenv("GMAIL_CLIENT_CONFIG", ""))
    gmail_token_json = _resolve_project_path(os.getenv("GMAIL_TOKEN_JSON", ""))
    gmail_max_results = _clamp_int(
        _as_int(os.getenv("GMAIL_MAX_RESULTS"), DEFAULT_GMAIL_MAX_RESULTS),
        minimum=1,
        maximum=MAX_GMAIL_MAX_RESULTS,
    )
    return Settings(
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
        google_application_credentials=credentials_path,
        gmail_client_config=gmail_client_config,
        gmail_token_json=gmail_token_json,
        gmail_label_name=os.getenv("GMAIL_LABEL_NAME", "Job Tracker"),
        gmail_max_results=gmail_max_results,
        dry_run=_as_bool(os.getenv("JOB_TRACKER_DRY_RUN"), default=True),
    )
