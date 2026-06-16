from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    config_dir: Path = CONFIG_DIR
    scoring_rules_path: Path = CONFIG_DIR / "scoring_rules.yml"
    target_profile_path: Path = CONFIG_DIR / "target_profile.yml"
    google_sheet_id: str = ""
    google_application_credentials: str = ""
    dry_run: bool = True


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
        google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        dry_run=_as_bool(os.getenv("JOB_TRACKER_DRY_RUN"), default=True),
    )
