# Copyright 2026 Duke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Slicer detection for different platforms."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slicer:
    key: str
    display: str
    default_profile_dirs: list[Path]  # user may override


def _detect_user_dirs(base: Path) -> list[Path]:
    """
    Find numeric user_id subdirectories under base/user/.
    e.g. ~/Library/Application Support/BambuStudio/user/12345/
    Returns sorted list of discovered user dirs.
    """
    user_root = base / "user"
    if not user_root.exists():
        return []

    found = []
    for entry in user_root.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            found.append(entry)
    return sorted(found, key=lambda p: p.name)


def _detect_creality_version(app_support: Path) -> list[Path]:
    """
    Detect Creality Print installation directory.
    Checks for version 7.0, then 6.0 if not found.
    Format: ~/Library/Application Support/Creality/Creality Print/7.0/
    """
    creality_base = app_support / "Creality" / "Creality Print"

    # Try version 7 first, then version 6
    for version in ["7.0", "6.0"]:
        version_dir = creality_base / version
        if version_dir.exists():
            return [version_dir]

    # If neither exists, return empty list
    return []


def _macos_default_slicers() -> list[Slicer]:
    """
    macOS slicer profile locations (auto-detect numeric user_id subdirs).
    """
    home = Path.home()
    app_support = home / "Library" / "Application Support"

    # OrcaSlicer and variants
    orca_base = app_support / "OrcaSlicer"
    snapmaker_base = app_support / "SnapmakerOrcaSlicer"

    # Bambu Studio
    bambu_base = app_support / "BambuStudio"

    # Elegoo Slicer (based on OrcaSlicer)
    elegoo_base = app_support / "ElegooSlicer"

    orca_dirs = _detect_user_dirs(orca_base)
    snapmaker_dirs = _detect_user_dirs(snapmaker_base)
    bambu_dirs = _detect_user_dirs(bambu_base)
    creality_dirs = _detect_creality_version(app_support)
    elegoo_dirs = _detect_user_dirs(elegoo_base)

    return [
        Slicer(
            key="orcaslicer",
            display="Orca Slicer",
            default_profile_dirs=orca_dirs if orca_dirs else [
                orca_base / "user" / "default"],
        ),
        Slicer(
            key="bambustudio",
            display="Bambu Studio",
            default_profile_dirs=bambu_dirs if bambu_dirs else [
                bambu_base / "user" / "default"],
        ),
        Slicer(
            key="snapmakerorca",
            display="Snapmaker Orca",
            default_profile_dirs=snapmaker_dirs if snapmaker_dirs else [
                snapmaker_base / "user" / "default"],
        ),
        Slicer(
            key="crealityprint",
            display="Creality Print",
            default_profile_dirs=creality_dirs if creality_dirs else [
                app_support / "Creality" / "Creality Print" / "7.0"],
        ),
        Slicer(
            key="elegooslicer",
            display="Elegoo Slicer",
            default_profile_dirs=elegoo_dirs if elegoo_dirs else [
                elegoo_base / "user" / "default"],
        ),
    ]


def _windows_default_slicers() -> list[Slicer]:
    """
    Windows slicer profile locations (auto-detect numeric user_id subdirs).
    """
    # Windows uses %APPDATA% which is typically C:\Users\USERNAME\AppData\Roaming
    appdata = Path(os.getenv("APPDATA", ""))
    if not appdata or not appdata.exists():
        # Fallback to constructing the path manually
        appdata = Path.home() / "AppData" / "Roaming"

    # OrcaSlicer and variants
    orca_base = appdata / "OrcaSlicer"
    snapmaker_base = appdata / "SnapmakerOrcaSlicer"

    # Bambu Studio
    bambu_base = appdata / "BambuStudio"

    # Elegoo Slicer (based on OrcaSlicer)
    elegoo_base = appdata / "ElegooSlicer"

    # Creality Print on Windows
    # Typically in %APPDATA%\Creality\Creality Print\7.0
    creality_base = appdata / "Creality" / "Creality Print"

    orca_dirs = _detect_user_dirs(orca_base)
    snapmaker_dirs = _detect_user_dirs(snapmaker_base)
    bambu_dirs = _detect_user_dirs(bambu_base)
    elegoo_dirs = _detect_user_dirs(elegoo_base)

    # Detect Creality Print version on Windows
    creality_dirs = []
    for version in ["7.0", "6.0"]:
        version_dir = creality_base / version
        if version_dir.exists():
            creality_dirs = [version_dir]
            break

    return [
        Slicer(
            key="orcaslicer",
            display="Orca Slicer",
            default_profile_dirs=orca_dirs if orca_dirs else [
                orca_base / "user" / "default"],
        ),
        Slicer(
            key="bambustudio",
            display="Bambu Studio",
            default_profile_dirs=bambu_dirs if bambu_dirs else [
                bambu_base / "user" / "default"],
        ),
        Slicer(
            key="snapmakerorca",
            display="Snapmaker Orca",
            default_profile_dirs=snapmaker_dirs if snapmaker_dirs else [
                snapmaker_base / "user" / "default"],
        ),
        Slicer(
            key="crealityprint",
            display="Creality Print",
            default_profile_dirs=creality_dirs if creality_dirs else [
                creality_base / "7.0"],
        ),
        Slicer(
            key="elegooslicer",
            display="Elegoo Slicer",
            default_profile_dirs=elegoo_dirs if elegoo_dirs else [
                elegoo_base / "user" / "default"],
        ),
    ]


def get_default_slicers() -> list[Slicer]:
    """
    Get default slicer paths for the current platform.
    """
    system = platform.system()
    if system == "Darwin":  # macOS
        return _macos_default_slicers()
    elif system == "Windows":
        return _windows_default_slicers()
    else:  # Linux or other Unix-like
        # Linux paths are similar to macOS but in ~/.config or ~/.local/share
        # For now, use macOS-like paths as a fallback
        # TODO: Add proper Linux support
        return _macos_default_slicers()
