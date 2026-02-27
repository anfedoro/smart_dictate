from __future__ import annotations

from pathlib import Path

APP_NAME = "SmartDictate"


def base_dir() -> Path:
    return Path.home() / APP_NAME


def records_dir() -> Path:
    return base_dir() / "records"


def transcripts_dir() -> Path:
    return base_dir() / "transcripts"


def config_path() -> Path:
    return base_dir() / "config.toml"


def models_dir() -> Path:
    return base_dir() / "models"
