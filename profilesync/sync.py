"""File synchronization operations."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config
from .git import REPO_PROFILES_DIR, sha256_file
from .ui import highlight, info


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
    Returns list of (src, dst) copied.
    """
    copied: list[tuple[Path, Path]] = []
    repo_dir = cfg.repo_dir

    for slicer_key in cfg.enabled_slicers:
        dirs = [Path(p) for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
        dst_root = repo_dir / REPO_PROFILES_DIR / slicer_key
        dst_root.mkdir(parents=True, exist_ok=True)

        for d in dirs:
            if not d.exists():
                continue
            for src in d.rglob("*.json"):
                # Preserve relative structure under each configured dir to avoid filename collisions
                rel = src.relative_to(d)
                dst = dst_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists() and sha256_file(src) == sha256_file(dst):
                    continue
                shutil.copy2(src, dst)
                copied.append((src, dst))
    return copied


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


def display_grouped_files(grouped: dict[str, dict[str, list[tuple[Path, Path]]]], message: str) -> None:
    """
    Display files grouped by slicer and profile type.
    """
    total = sum(len(files) for types in grouped.values()
                for files in types.values())
    print(message.format(count=total))

    for slicer_key, types in grouped.items():
        display_name = SLICER_DISPLAY_NAMES.get(
            slicer_key, slicer_key.capitalize())
        total_for_slicer = sum(len(files) for files in types.values())

        print(f"\n  {highlight(display_name)} ({total_for_slicer} files):")

        for profile_type, files in sorted(types.items()):
            print(f"    {info(profile_type)} ({len(files)}):")
            # Show first 2 files for each type
            for src, dst in files[:2]:
                print(f"      • {dst.name}")
            if len(files) > 2:
                print(f"      ... and {len(files) - 2} more")


def is_json_file(p: Path) -> bool:
    """Check if path is a JSON file."""
    return p.is_file() and p.suffix.lower() == ".json"
