"""Configuration management."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Default directories - store in the same directory as the package
# This keeps everything portable and local to the project
SCRIPT_DIR = Path(__file__).parent.parent.resolve()  # Go up to project root
DEFAULT_CONFIG_DIR = SCRIPT_DIR
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"


@dataclass
class Config:
    """Application configuration."""

    github_remote: str
    repo_dir: Path
    enabled_slicers: list[str]
    slicer_profile_dirs: dict[str, list[str]]  # key -> list of dir paths
    editor_cmd: Optional[str] = None  # e.g. "code --wait" or "vim"

    @staticmethod
    def path() -> Path:
        return DEFAULT_CONFIG_DIR / "config.json"

    def save(self) -> None:
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = dataclasses.asdict(self)
        payload["repo_dir"] = str(self.repo_dir)
        with Config.path().open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    @staticmethod
    def load() -> "Config":
        p = Config.path()
        if not p.exists():
            raise FileNotFoundError(f"No config found at {p}")
        with p.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return Config(
            github_remote=payload["github_remote"],
            repo_dir=Path(payload.get("repo_dir", str(
                DEFAULT_DATA_DIR / "profiles"))),
            enabled_slicers=list(payload.get("enabled_slicers", [])),
            slicer_profile_dirs=dict(payload.get("slicer_profile_dirs", {})),
            editor_cmd=payload.get("editor_cmd"),
        )
