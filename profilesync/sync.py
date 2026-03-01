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

"""File synchronization operations."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config
from .git import REPO_PROFILES_DIR, sha256_file

# Map config keys to proper display names
SLICER_DISPLAY_NAMES = {
    "orcaslicer": "Orca Slicer",
    "bambustudio": "Bambu Studio",
    "snapmakerorca": "Snapmaker Orca",
    "crealityprint": "Creality Print",
    "elegooslicer": "Elegoo Slicer",
}


def export_from_slicers_to_repo(cfg: Config) -> list[tuple[Path, Path]]:
    """
    Copy JSON files from slicer profile dirs -> repo/profiles/<slicer>/
    Also deletes files from repo that no longer exist in slicer dirs.
    Returns list of (src, dst) copied.
    """
    copied: list[tuple[Path, Path]] = []
    repo_dir = cfg.repo_dir

    for slicer_key in cfg.enabled_slicers:
        dirs = [Path(p) for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        dst_root = repo_dir / REPO_PROFILES_DIR / slicer_key
        dst_root.mkdir(parents=True, exist_ok=True)

        # Track which files should exist in the repo (based on slicer)
        expected_repo_files: set[Path] = set()

        for d in dirs:
            if not d.exists():
                continue
            for src in d.rglob("*.json"):
                # Preserve relative structure under each configured dir to avoid filename collisions
                rel = src.relative_to(d)
                dst = dst_root / rel
                expected_repo_files.add(dst)

                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists() and sha256_file(src) == sha256_file(dst):
                    continue
                shutil.copy2(src, dst)
                copied.append((src, dst))

        # Delete files from repo that no longer exist in slicer
        if dst_root.exists():
            for repo_file in dst_root.rglob("*.json"):
                if repo_file not in expected_repo_files:
                    repo_file.unlink()
                    # Track deletions as special entries (use None as src to indicate deletion)
                    copied.append((None, repo_file))  # type: ignore

    return copied


def rebuild_exported_from_git(cfg: Config) -> list[tuple[Path, Path]]:
    """
    Reconstruct the exported (src, dst) list from uncommitted git changes.

    When export_from_slicers_to_repo already copied files in a previous run
    but they were never pushed, ``exported`` will be empty even though the repo
    has uncommitted changes. This function reads ``git status`` and maps the
    changed repo files back to their slicer source paths.
    """
    from .git import git_status_porcelain, _git_unescape

    status = git_status_porcelain(cfg.repo_dir)
    if not status.strip():
        return []

    result: list[tuple[Path, Path]] = []
    for line in status.strip().splitlines():
        # porcelain format: "XY path" (first 3 chars are status + space)
        if len(line) < 4:
            continue
        status_code = line[:2]
        raw_path = line[3:].strip()
        # Git wraps paths with special chars in quotes + escapes them
        if raw_path.startswith('"') and raw_path.endswith('"'):
            raw_path = _git_unescape(raw_path[1:-1])
        rel_path = Path(raw_path)
        dst = cfg.repo_dir / rel_path

        if not str(rel_path).startswith(str(REPO_PROFILES_DIR)):
            continue

        # Deleted from repo
        if "D" in status_code:
            result.append((None, dst))  # type: ignore
            continue

        # Map repo path back to slicer source
        # repo structure: profiles/<slicer_key>/<type>/<file>.json
        try:
            profile_rel = rel_path.relative_to(REPO_PROFILES_DIR)
            parts = profile_rel.parts
            if len(parts) < 2:
                continue
            slicer_key = parts[0]
            sub_rel = Path(*parts[1:])  # type/file.json
        except (ValueError, IndexError):
            continue

        # Find the slicer source file
        dirs = [Path(p) for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        src = None
        for d in dirs:
            candidate = d / sub_rel
            if candidate.exists():
                src = candidate
                break

        if src is not None:
            result.append((src, dst))
        elif dst.exists():
            # File exists in repo but not in slicer (shouldn't happen often)
            result.append((dst, dst))

    return result


def import_from_repo_to_slicers(cfg: Config) -> list[tuple[Path, Path]]:
    """
    Copy JSON files from repo/profiles/<slicer>/ -> slicer profile dirs (into the first configured dir).
    Returns list of (src, dst) copied.
    """
    copied: list[tuple[Path, Path]] = []
    repo_dir = cfg.repo_dir

    for slicer_key in cfg.enabled_slicers:
        src_root = repo_dir / REPO_PROFILES_DIR / slicer_key
        if not src_root.exists():
            continue

        dst_dirs = [Path(p)
                    for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        if not dst_dirs:
            continue
        dst_base = dst_dirs[0]
        dst_base.mkdir(parents=True, exist_ok=True)

        for src in src_root.rglob("*.json"):
            rel = src.relative_to(src_root)
            dst = dst_base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and sha256_file(src) == sha256_file(dst):
                continue
            shutil.copy2(src, dst)
            copied.append((src, dst))
    return copied


def group_by_slicer_and_type(files: list[tuple[Path, Path]], cfg: Config, repo_dir: Path, use_dst_for_type: bool = True) -> dict[str, dict[str, list[tuple[Path, Path]]]]:
    """
    Group files by slicer and then by profile type (filament/process/printer).

    Args:
        files: List of (src, dst) tuples
        cfg: Config object
        repo_dir: Repository directory path
        use_dst_for_type: If True, extract type from dst path (for export/push).
                         If False, extract from src path (for import/pull).

    Returns: {slicer_key: {profile_type: [(src, dst), ...]}}
    """
    by_slicer: dict[str, dict[str, list[tuple[Path, Path]]]] = {}

    for src, dst in files:
        # Determine which slicer this file belongs to
        slicer_key = None
        path_for_type = None
        base_path = None

        if use_dst_for_type:
            # For export: dst is in repo, check against repo structure
            for sk in cfg.enabled_slicers:
                slicer_root = repo_dir / REPO_PROFILES_DIR / sk
                if dst.is_relative_to(slicer_root):
                    slicer_key = sk
                    path_for_type = dst
                    base_path = slicer_root
                    break
        else:
            # For import: src is in repo, dst is in slicer dir, check dst against slicer dirs
            for sk in cfg.enabled_slicers:
                slicer_dirs = [Path(p)
                               for p in cfg.slicer_profile_dirs.get(sk, [])]
                for slicer_dir in slicer_dirs:
                    if dst.is_relative_to(slicer_dir):
                        slicer_key = sk
                        # Use src (from repo) to determine type
                        path_for_type = src
                        base_path = repo_dir / REPO_PROFILES_DIR / sk
                        break
                if slicer_key:
                    break

        if not slicer_key or not path_for_type or not base_path:
            continue

        # Get the profile type from the path (e.g., filament, process, printer)
        # Structure is: profiles/slicer/type/file.json or slicer_dir/type/file.json
        try:
            rel = path_for_type.relative_to(base_path)
            # First part of path is the type (filament, process, printer, etc.)
            profile_type = rel.parts[0] if rel.parts else "other"
            # Capitalize first letter
            profile_type = profile_type.capitalize()
        except (ValueError, IndexError):
            profile_type = "Other"

        # Initialize nested dict structure
        if slicer_key not in by_slicer:
            by_slicer[slicer_key] = {}
        if profile_type not in by_slicer[slicer_key]:
            by_slicer[slicer_key][profile_type] = []

        by_slicer[slicer_key][profile_type].append((src, dst))

    return by_slicer


def collect_server_profiles(cfg: Config) -> list[dict]:
    """
    Collect all profiles available on the server (in the repo) and flag
    whether each one already exists locally in the slicer folder.

    Returns a list of dicts:
        {
            "slicer_key": str,
            "profile_type": str,       # e.g. Filament, Process, Printer
            "filename": str,
            "repo_path": Path,         # absolute path inside repo
            "local_path": Path | None, # matching slicer path or None
            "matches_local": bool,     # True if hash matches / identical
            "rel": PurePosixPath,      # relative path within slicer root
        }
    """
    results: list[dict] = []
    repo_dir = cfg.repo_dir

    for slicer_key in cfg.enabled_slicers:
        src_root = repo_dir / REPO_PROFILES_DIR / slicer_key
        if not src_root.exists():
            continue

        dst_dirs = [Path(p)
                    for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        dst_base = dst_dirs[0] if dst_dirs else None

        for src in src_root.rglob("*.json"):
            rel = src.relative_to(src_root)
            profile_type = rel.parts[0].capitalize() if rel.parts else "Other"

            local_path = dst_base / rel if dst_base else None
            matches = False
            if local_path and local_path.exists():
                matches = sha256_file(src) == sha256_file(local_path)

            results.append({
                "slicer_key": slicer_key,
                "profile_type": profile_type,
                "filename": src.name,
                "repo_path": src,
                "local_path": local_path,
                "matches_local": matches,
                "rel": rel,
            })

    return results


def import_selected_profiles(cfg: Config, selected: list[dict]) -> list[tuple[Path, Path]]:
    """
    Import only the selected profiles from repo to slicer dirs.

    Args:
        cfg: Config object
        selected: list of profile dicts as returned by collect_server_profiles()

    Returns list of (src, dst) actually copied.
    """
    copied: list[tuple[Path, Path]] = []

    for entry in selected:
        slicer_key = entry["slicer_key"]
        src = entry["repo_path"]
        rel = entry["rel"]

        dst_dirs = [Path(p)
                    for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        if not dst_dirs:
            continue
        dst_base = dst_dirs[0]
        dst = dst_base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists() and sha256_file(src) == sha256_file(dst):
            continue  # already identical
        shutil.copy2(src, dst)
        copied.append((src, dst))

    return copied


def export_selected_to_repo(cfg: Config, selected_files: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """
    Copy only the selected (src, dst) pairs to the repo.
    Handles both modifications (src is a Path) and deletions (src is None).

    Returns list of (src, dst) that were actually processed.
    """
    processed: list[tuple[Path, Path]] = []

    for src, dst in selected_files:
        if src is None:
            # Deletion
            if dst.exists():
                dst.unlink()
                processed.append((None, dst))  # type: ignore
        else:
            # Copy
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and sha256_file(src) == sha256_file(dst):
                continue
            shutil.copy2(src, dst)
            processed.append((src, dst))

    return processed
