#!/usr/bin/env python3
"""
profilesync (v2 - Cross-platform)

Interactive sync for slicer filament/profile JSON files using a GitHub private repo.
- Supports: Bambu Studio, OrcaSlicer, and more (paths auto-detected)
- Platforms: macOS, Windows, Linux
- Uses git CLI (must be installed) and SSH auth recommended.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
try:
    import readline  # Enable arrow keys and history in input() (Unix/macOS only)
except ImportError:
    pass  # readline not available on Windows, but not required
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Cross-platform colored terminal output
COLORAMA_AVAILABLE = False
try:
    import colorama
    colorama.init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    # colorama not installed - colors will be disabled
    pass


APP_NAME = "profilesync"
# Store config in the same directory as the script
SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_CONFIG_DIR = SCRIPT_DIR
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"


# ---- Color utilities -----------------------------------------------------------

class Colors:
    """ANSI color codes for terminal output"""
    # Basic colors
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[34m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'

    # Styles
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


def color(text: str, color_code: str, bold: bool = False) -> str:
    """Wrap text in ANSI color codes (cross-platform with colorama)"""
    # Disable colors if not in a TTY or colorama not available
    if not sys.stdout.isatty() or not COLORAMA_AVAILABLE:
        return text

    prefix = f"{Colors.BOLD}{color_code}" if bold else color_code
    return f"{prefix}{text}{Colors.RESET}"


def success(text: str) -> str:
    """Green text for success messages"""
    return color(text, Colors.GREEN)


def warning(text: str) -> str:
    """Yellow text for warnings"""
    return color(text, Colors.YELLOW)


def error(text: str) -> str:
    """Red text for errors"""
    return color(text, Colors.RED)


def info(text: str) -> str:
    """Magenta text for informational messages (counts, etc.)"""
    return color(text, Colors.MAGENTA)


def highlight(text: str) -> str:
    """Bold white text for emphasis"""
    return color(text, Colors.WHITE, bold=True)


def dim(text: str) -> str:
    """Dimmed text for less important info - using blue for better readability"""
    return color(text, Colors.BLUE)


def get_check_symbol() -> str:
    """Get appropriate check/success symbol for the platform"""
    # Use ASCII-compatible symbols for Windows compatibility
    # Unicode checkmarks don't display properly in Windows terminal
    system = platform.system()
    if system == "Windows":
        return "[OK]"  # ASCII-safe for Windows
    else:
        return "✓"  # Unicode checkmark for Unix/macOS


# ---- Slicer definitions (macOS) ------------------------------------------------

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


def _get_default_slicers() -> list[Slicer]:
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


# ---- Config -------------------------------------------------------------------

@dataclass
class Config:
    github_remote: str  # e.g. git@github.com:you/yourrepo.git
    repo_dir: Path
    enabled_slicers: list[str]
    # key -> list of dirs (strings for JSON)
    slicer_profile_dirs: dict[str, list[str]]
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


# ---- Utilities ----------------------------------------------------------------

def run(cmd: list[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    ans = input(prompt + suffix).strip().lower()
    if ans == "":
        return default
    return ans in ("y", "yes")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_json_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() == ".json"


def now_iso() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def get_computer_id() -> str:
    """
    Get a human-readable identifier for this computer.
    Returns something like "Synced from macOS (duke@MacBook-Pro)" or "Synced from Windows (john@DESKTOP-ABC123)"
    """
    system = platform.system()
    username = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
    hostname = platform.node().split('.')[0]  # Remove domain if present

    # Friendly OS names
    os_name = {
        "Darwin": "macOS",
        "Windows": "Windows",
        "Linux": "Linux"
    }.get(system, system)

    return f"Synced from {os_name} ({username}@{hostname})"


# ---- Git operations ------------------------------------------------------------

def ensure_git_available() -> None:
    try:
        run(["git", "--version"])
    except Exception as e:
        raise RuntimeError(
            "git not found. Install Xcode Command Line Tools or git.") from e


def validate_git_remote(remote: str) -> tuple[bool, str]:
    """
    Validate a git remote URL format and check if we have access to it.
    Returns (is_valid, error_message).
    """
    remote = remote.strip()

    # Basic format validation
    if not remote:
        return False, "Remote URL is empty"

    # Check for common patterns
    is_ssh = remote.startswith("git@")
    is_https = remote.startswith("https://") or remote.startswith("http://")

    if not (is_ssh or is_https):
        return False, "Remote URL must be SSH (git@...) or HTTPS (https://...)"

    # SSH format validation: git@host:user/repo.git
    if is_ssh:
        if ":" not in remote:
            return False, "SSH URL format invalid (expected git@host:user/repo.git)"

    # HTTPS format validation
    if is_https:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(remote)
            if not parsed.netloc:
                return False, "HTTPS URL format invalid"
        except Exception:
            return False, "HTTPS URL format invalid"

    # Test access by doing ls-remote (lightweight, doesn't clone)
    print(f"Checking access to {remote}...")
    result = run(["git", "ls-remote", remote, "HEAD"], check=False)

    if result.returncode != 0:
        err_msg = result.stderr.strip()
        if "Could not resolve host" in err_msg or "Could not read from remote" in err_msg:
            return False, f"Cannot reach remote repository. Check URL and network connection.\n{err_msg}"
        elif "Permission denied" in err_msg or "Authentication failed" in err_msg:
            return False, f"Access denied. Check your SSH keys or credentials.\n{err_msg}"
        elif "Repository not found" in err_msg or "does not appear to be a git repository" in err_msg:
            return False, f"Repository not found or not accessible.\n{err_msg}"
        else:
            return False, f"Cannot access remote repository.\n{err_msg}"

    return True, ""


def clone_or_open_repo(repo_dir: Path, remote: str) -> None:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if (repo_dir / ".git").exists():
        # Ensure remote is set
        r = run(["git", "remote", "get-url", "origin"],
                cwd=repo_dir, check=False)
        if r.returncode != 0:
            run(["git", "remote", "add", "origin", remote], cwd=repo_dir)
        else:
            current = r.stdout.strip()
            if current != remote:
                print(
                    f"Warning: repo origin is {current}, config expects {remote}")
        return

    run(["git", "clone", remote, str(repo_dir)])


def git_pull_rebase(repo_dir: Path) -> None:
    run(["git", "fetch", "--all"], cwd=repo_dir)

    # Check if remote branch exists before trying to pull
    result = run(["git", "rev-parse", "--verify", "origin/main"],
                 cwd=repo_dir, check=False)
    if result.returncode != 0:
        # Remote branch doesn't exist yet, nothing to pull
        return

    # Check if there are any changes to pull
    local_head = run(["git", "rev-parse", "HEAD"],
                     cwd=repo_dir, check=False).stdout.strip()
    remote_head = run(["git", "rev-parse", "origin/main"],
                      cwd=repo_dir, check=False).stdout.strip()

    if local_head == remote_head:
        # Already up to date, no need to rebase
        return

    # Check if there are uncommitted changes that would conflict with rebase
    status = git_status_porcelain(repo_dir)
    if status.strip():
        # There are uncommitted changes - stash them temporarily
        run(["git", "stash", "push", "-m",
            "profilesync auto-stash before pull"], cwd=repo_dir)
        try:
            # Use rebase explicitly for better cross-platform compatibility
            run(["git", "rebase", "origin/main"], cwd=repo_dir)
        finally:
            # Try to restore stashed changes (may conflict, which is okay)
            result = run(["git", "stash", "pop"], cwd=repo_dir, check=False)
            if result.returncode != 0:
                # Stash pop failed (likely due to conflicts) - drop the stash
                # The files are already in the working directory from the pull
                run(["git", "stash", "drop"], cwd=repo_dir, check=False)
    else:
        # No uncommitted changes, safe to rebase
        run(["git", "rebase", "origin/main"], cwd=repo_dir)


def git_has_commits(repo_dir: Path) -> bool:
    """Check if repo has any commits."""
    result = run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False)
    return result.returncode == 0


def git_has_conflicts(repo_dir: Path) -> bool:
    """Check if there are actual merge/rebase conflicts."""
    # Check for rebase in progress
    if (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists():
        return True
    # Check for merge in progress
    if (repo_dir / ".git" / "MERGE_HEAD").exists():
        return True
    # Check git status for conflict markers
    status = git_status_porcelain(repo_dir)
    return any(line.startswith("UU ") or line.startswith("AA ") or line.startswith("DD ")
               for line in status.splitlines())


def git_get_conflicted_files(repo_dir: Path) -> list[Path]:
    """Get list of files with conflicts."""
    status = git_status_porcelain(repo_dir)
    conflicted = []
    for line in status.splitlines():
        if line.startswith(("UU ", "AA ", "DD ", "AU ", "UA ", "DU ", "UD ")):
            # Format is "XY filename"
            filepath = line[3:].strip()

            # Git wraps filenames with special chars in quotes and escapes them
            # e.g. "profiles/orcaslicer/filament/Bambu ABS - Tuned.json"
            if filepath.startswith('"') and filepath.endswith('"'):
                # Remove quotes and decode escape sequences
                filepath = filepath[1:-1]
                # Decode common escape sequences (\t, \n, \\, \", etc.)
                filepath = filepath.encode('utf-8').decode('unicode_escape')

            conflicted.append(repo_dir / filepath)
    return conflicted


def git_remote_has_profiles(repo_dir: Path) -> bool:
    """
    Check if origin/main actually has any profile .json files.
    Returns True only if profiles have been pushed to GitHub.
    """
    # First, check if origin/main exists
    result = run(
        ["git", "rev-parse", "--verify", "origin/main"],
        cwd=repo_dir,
        check=False
    )
    if result.returncode != 0:
        return False

    # List files in profiles/ directory on origin/main
    result = run(
        ["git", "ls-tree", "-r", "--name-only",
            "origin/main", str(REPO_PROFILES_DIR)],
        cwd=repo_dir,
        check=False
    )

    if result.returncode != 0:
        return False

    # Check if any .json files exist
    files = result.stdout.strip().splitlines()
    return any(f.endswith('.json') for f in files)


def initialize_empty_repo(repo_dir: Path, remote: str) -> None:
    print(info("\nSetting up sync folder for the first time..."))

    # Create initial README
    readme_path = repo_dir / "README.md"
    readme_path.write_text(textwrap.dedent("""\
        # Slicer Profile Sync

        This repository contains synced 3D printer slicer profiles.

        Managed by profilesync tool.
    """))

    # Create profiles directory structure
    profiles_dir = repo_dir / REPO_PROFILES_DIR
    profiles_dir.mkdir(exist_ok=True)
    (profiles_dir / ".gitkeep").touch()

    # Commit locally but don't push yet - let user decide when to push
    run(["git", "add", "-A"], cwd=repo_dir)
    run(["git", "commit", "-m", "Initial setup"], cwd=repo_dir)

    # Set up main branch
    result = run(["git", "branch", "-M", "main"], cwd=repo_dir, check=False)
    if result.returncode != 0:
        run(["git", "branch", "-M", "master"], cwd=repo_dir)

    print(success(f"{get_check_symbol()} Sync folder ready") +
          dim(" (not yet pushed to GitHub)"))


def git_status_porcelain(repo_dir: Path) -> str:
    return run(["git", "status", "--porcelain"], cwd=repo_dir).stdout


def git_head_info(repo_dir: Path) -> str:
    # Return "shortsha date subject"
    out = run(["git", "log", "-1", "--pretty=format:%h %cI %s"],
              cwd=repo_dir, check=False).stdout.strip()
    return out or "(no commits)"


def git_commit_if_needed(repo_dir: Path, message: str) -> bool:
    run(["git", "add", "-A"], cwd=repo_dir)
    st = git_status_porcelain(repo_dir)
    if not st.strip():
        return False
    run(["git", "commit", "-m", message], cwd=repo_dir)
    return True


def git_push(repo_dir: Path) -> None:
    run(["git", "push"], cwd=repo_dir)


def git_list_commits(repo_dir: Path, limit: int = 20) -> list[str]:
    out = run(["git", "log", f"-{limit}", "--pretty=format:%h %cI %s"],
              cwd=repo_dir, check=False).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def git_checkout_commit(repo_dir: Path, commit: str) -> None:
    # Detached HEAD is fine for "pull a version to slicer"
    run(["git", "checkout", commit], cwd=repo_dir)


def git_checkout_branch(repo_dir: Path, branch: str = "main") -> None:
    run(["git", "checkout", branch], cwd=repo_dir)


# ---- File sync model -----------------------------------------------------------
# We keep slicer exports in the repo under: profiles/<slicer_key>/*.json
REPO_PROFILES_DIR = Path("profiles")

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


# ---- Repo safety guards --------------------------------------------------------

def _suggest_repo_dir_from_remote(remote: str) -> Path:
    """
    Best-effort: derive a stable local clone dir name from remote.
    Supports SSH: git@github.com:USER/REPO.git
    Supports HTTPS: https://github.com/USER/REPO.git
    """
    name = "profiles"
    r = remote.strip()

    if r.startswith("git@") and ":" in r:
        # git@github.com:USER/REPO.git
        path = r.split(":", 1)[1]
        name = path.rsplit("/", 1)[-1]
    else:
        try:
            u = urlparse(r)
            if u.path:
                name = u.path.rstrip("/").rsplit("/", 1)[-1]
        except Exception:
            pass

    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        name = "profiles"

    return DEFAULT_DATA_DIR / name


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _find_git_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(50):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _guard_not_dev_repo(repo_dir: Path) -> None:
    """
    Prevent users from accidentally pointing the sync repo at the same repo
    they're developing profilesync within.
    Allow data/ subdirectory for portable local storage.
    """
    dev_root = _find_git_root(Path(__file__).parent)
    if not dev_root:
        return

    # Allow if repo_dir is under the designated data directory
    if _is_inside(repo_dir, DEFAULT_DATA_DIR):
        return

    # Block if trying to use dev repo root or would conflict with .git
    if _is_inside(repo_dir, dev_root):
        raise RuntimeError(
            f"Refusing to use repo_dir inside the profilesync dev repo.\n"
            f"  dev repo: {dev_root}\n"
            f"  repo_dir: {repo_dir}\n"
            f"Use the default data/ directory or choose a location outside this repo."
        )


# ---- UI / flows ----------------------------------------------------------------

def interactive_select_slicers(slicers: list[Slicer]) -> list[str]:
    print("Select slicers to sync (comma-separated numbers):")
    for i, s in enumerate(slicers, start=1):
        print(f"  {i}. {s.display}")
    raw = input("Selection: ").strip()
    if not raw:
        return []
    idxs = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idxs.add(int(part))
    chosen = []
    for i, s in enumerate(slicers, start=1):
        if i in idxs:
            chosen.append(s.key)
    return chosen


def interactive_configure_paths(enabled: list[str], slicers: list[Slicer]) -> dict[str, list[str]]:
    by_key = {s.key: s for s in slicers}
    result: dict[str, list[str]] = {}
    for key in enabled:
        s = by_key[key]
        # Use the first detected directory or fallback
        default_dir = s.default_profile_dirs[0] if s.default_profile_dirs else None

        if default_dir:
            exists_marker = get_check_symbol() if default_dir.exists() else "X"
            print(f"\n{s.display}: [{exists_marker}] {default_dir}")
            print("  Press Enter to use this directory, or enter a custom path:")
        else:
            print(f"\n{s.display}: (No directory auto-detected)")
            print("  Enter the profile directory path:")

        raw = input("> ").strip()
        if not raw:
            result[key] = [str(default_dir)] if default_dir else []
        else:
            result[key] = [raw]
    return result


def show_local_remote_summary(cfg: Config) -> None:
    print(f"\n{highlight('Sync status:')}")
    print(f"  {dim('Local folder:')} {cfg.repo_dir}")
    print(f"  {dim('GitHub repo:')}  {cfg.github_remote}")

    # Fetch latest from remote to ensure we have up-to-date info
    # Use --prune to remove stale remote-tracking branches
    run(["git", "fetch", "origin", "--prune"], cwd=cfg.repo_dir, check=False)

    # Check if profiles actually exist on GitHub (origin/main)
    if git_remote_has_profiles(cfg.repo_dir):
        # Get the last commit timestamp from origin/main that touched profiles
        result = run(
            ["git", "log", "-1", "--pretty=format:%cI",
                "origin/main", "--", str(REPO_PROFILES_DIR)],
            cwd=cfg.repo_dir,
            check=False
        )

        if result.returncode == 0 and result.stdout.strip():
            out = result.stdout.strip()
            # Parse and format the timestamp nicely
            try:
                dt = datetime.fromisoformat(out.replace('Z', '+00:00'))
                time_str = dt.strftime("%B %d, %Y at %I:%M %p")
                print(f"  {dim('Last sync:')}    {info(time_str)}")
            except Exception:
                print(f"  {dim('Last sync:')}    {out}")
        else:
            print(f"  {dim('Last sync:')}    Unknown")
    else:
        print(f"  {dim('Last sync:')}    Never (no profiles on GitHub yet)")

    # Show a quick file inventory + mtimes from sync directory
    for slicer in cfg.enabled_slicers:
        root = cfg.repo_dir / REPO_PROFILES_DIR / slicer
        if not root.exists():
            continue
        files = sorted([p for p in root.rglob("*.json")])
        if not files:
            continue
        newest = max(files, key=lambda p: p.stat().st_mtime)
        newest_time = datetime.fromtimestamp(
            newest.stat().st_mtime).strftime("%B %d, %Y at %I:%M %p")
        display_name = SLICER_DISPLAY_NAMES.get(slicer, slicer.capitalize())
        print(f"\n  {highlight(display_name)}:")
        print(f"    {info(str(len(files)) + ' profile files')}")
        print(f"    {dim('Last modified:')} {newest.name}")
        print(f"    {dim(newest_time)}")


def open_editor(cfg: Config, path: Path) -> None:
    cmd = cfg.editor_cmd or os.environ.get(
        "GIT_EDITOR") or os.environ.get("EDITOR")

    if not cmd:
        print(
            "No editor configured. Set config.editor_cmd or environment EDITOR/GIT_EDITOR.")
        return

    # Handle VS Code on macOS specifically
    if "code" in cmd and not shutil.which("code"):
        # Try common VS Code paths on macOS
        vscode_paths = [
            "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
            os.path.expanduser(
                "~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        ]

        for vscode_path in vscode_paths:
            if os.path.exists(vscode_path):
                cmd = cmd.replace("code", f'"{vscode_path}"')
                break
        else:
            # Still can't find VS Code, try using 'open' on macOS
            print("VS Code command not found in PATH. Using default editor...")
            subprocess.run(["open", "-e", str(path)], check=False)
            return

    # Split command carefully (handle quotes)
    try:
        import shlex
        parts = shlex.split(cmd)
    except:
        parts = cmd.split()

    parts.append(str(path))
    subprocess.run(parts, check=False)


def interactive_resolve_conflicts(cfg: Config, repo_dir: Path) -> bool:
    """
    Guide user through conflict resolution interactively.
    Returns True if conflicts were resolved, False if user wants to abort.
    """
    conflicted_files = git_get_conflicted_files(repo_dir)

    if not conflicted_files:
        print(error("\nNo conflicted files found, but sync operation failed."))
        print("Please check sync folder manually:")
        print(f"  cd {repo_dir}")
        print(f"  git status")
        return False

    print("\n" + "="*60)
    print(warning("MERGE CONFLICTS DETECTED"))
    print("="*60)
    print("\nThe following files have conflicts:")

    # Group by slicer for display
    by_slicer: dict[str, list[Path]] = {}
    for f in conflicted_files:
        # Determine which slicer this file belongs to
        try:
            rel = f.relative_to(repo_dir / REPO_PROFILES_DIR)
            slicer_key = rel.parts[0] if rel.parts else None
            if slicer_key and slicer_key in cfg.enabled_slicers:
                if slicer_key not in by_slicer:
                    by_slicer[slicer_key] = []
                by_slicer[slicer_key].append(f)
        except ValueError:
            # File not in profiles directory, show full relative path
            print(f"  • {f.relative_to(repo_dir)}")

    for slicer_key, files in by_slicer.items():
        display_name = SLICER_DISPLAY_NAMES.get(
            slicer_key, slicer_key.capitalize())
        print(f"\n  {display_name}:")
        for f in files:
            print(f"    • {f.name}")

    print("\nYour changes and GitHub's changes both modified the same files.")
    print("You need to choose which changes to keep.")

    if not confirm("\nOpen files in editor to resolve conflicts?", default=True):
        print("\nTo resolve manually later:")
        print(f"  cd {repo_dir}")
        print(f"  # Edit conflicted files, then:")
        print(f"  git add -A")
        print(f"  git rebase --continue")
        return False

    # Open each conflicted file in editor
    for conflict_file in conflicted_files:
        # Determine slicer name for display
        try:
            rel = conflict_file.relative_to(repo_dir / REPO_PROFILES_DIR)
            slicer_key = rel.parts[0] if rel.parts else None
            if slicer_key and slicer_key in cfg.enabled_slicers:
                display_name = SLICER_DISPLAY_NAMES.get(
                    slicer_key, slicer_key.capitalize())
                print(f"\nOpening {display_name}: {conflict_file.name}")
            else:
                print(f"\nOpening: {conflict_file.relative_to(repo_dir)}")
        except ValueError:
            print(f"\nOpening: {conflict_file.relative_to(repo_dir)}")

        print("Look for conflict markers: <<<<<<<, =======, >>>>>>>")
        print("Edit the file to keep the changes you want, then save and close.")

        if not confirm("Ready to open in editor?", default=True):
            continue

        # Pass absolute path to editor
        open_editor(cfg, conflict_file.resolve())

    print("\n" + "="*60)
    print("After editing, git needs to know the conflicts are resolved.")

    if confirm("Mark all conflicts as resolved and continue?", default=True):
        run(["git", "add", "-A"], cwd=repo_dir)

        # Check if we're in a rebase
        if (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists():
            # Set environment to avoid editor prompts
            env = os.environ.copy()
            # Use 'true' command (does nothing, returns success)
            env["GIT_EDITOR"] = "true"

            result = subprocess.run(
                ["git", "rebase", "--continue"],
                cwd=str(repo_dir),
                env=env,
                text=True,
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                print("\nRebase failed. You may need to resolve more conflicts.")
                print(f"Output: {result.stderr}")
                print(f"Check: {repo_dir}")
                return False
        else:
            # Regular merge - ensure no editor is invoked
            env = os.environ.copy()
            env["GIT_EDITOR"] = "true"

            result = subprocess.run(
                ["git", "commit", "--no-edit"],
                cwd=str(repo_dir),
                env=env,
                text=True,
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                print(error("\nCommit failed."))
                print(f"Output: {result.stderr}")
                print(f"Check: {repo_dir}")
                return False

        print(
            success(f"{get_check_symbol()} Conflicts resolved successfully!"))
        return True
    else:
        # User declined to resolve conflicts - abort the rebase/merge
        print(warning("\nAborting sync..."))

        # Check if we're in a rebase
        if (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists():
            result = run(["git", "rebase", "--abort"],
                         cwd=repo_dir, check=False)
            if result.returncode == 0:
                print(success(f"{get_check_symbol()} Sync cancelled.") +
                      " Your local files are unchanged.")
            else:
                print(warning("⚠ Warning:") +
                      " Could not automatically abort. Check the sync folder:")
                print(f"  cd {repo_dir}")
                print(f"  git rebase --abort")
        else:
            # Regular merge - reset to previous state
            result = run(["git", "merge", "--abort"],
                         cwd=repo_dir, check=False)
            if result.returncode == 0:
                print(success(f"{get_check_symbol()} Sync cancelled.") +
                      " Your local files are unchanged.")
            else:
                print(warning("⚠ Warning:") +
                      " Could not automatically abort. Check the sync folder:")
                print(f"  cd {repo_dir}")
                print(f"  git merge --abort")

        return False


# ---- Commands ------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    ensure_git_available()

    slicers = _get_default_slicers()

    if args.remote:
        remote = args.remote
    else:
        print("GitHub remote (SSH recommended), e.g. git@github.com:USER/REPO.git")
        remote = input("Remote URL: ").strip()
        if not remote:
            print("Remote URL is required.")
            return 2

    # Validate the remote URL and check access
    is_valid, error_msg = validate_git_remote(remote)
    if not is_valid:
        print(error(f"\nError: {error_msg}"))
        return 2

    print(success(f"{get_check_symbol()} Remote repository is accessible\n"))

    suggested_repo_dir = _suggest_repo_dir_from_remote(remote)

    if args.repo_dir:
        repo_dir = Path(args.repo_dir).expanduser()
    else:
        print(
            f"Local clone directory (press Enter to use default):\n  {suggested_repo_dir}")
        repo_dir_raw = input("Repo dir: ").strip()
        repo_dir = Path(repo_dir_raw).expanduser(
        ) if repo_dir_raw else suggested_repo_dir

    _guard_not_dev_repo(repo_dir)

    enabled = interactive_select_slicers(slicers)
    if not enabled:
        print("No slicers selected.")
        return 2

    paths = interactive_configure_paths(enabled, slicers)

    clone_or_open_repo(repo_dir, remote)

    # Configure editor for conflict resolution
    if args.editor:
        editor_cmd = args.editor
    else:
        print("\nSelect editor for conflict resolution:")

        # Get system default editor
        git_editor = os.environ.get("GIT_EDITOR") or os.environ.get("EDITOR")

        editor_choices = [
            ("vim", "Vim"),
            ("nano", "Nano"),
            ("subl -w", "Sublime Text"),
            ("code --wait", "VS Code"),
        ]

        for i, (cmd, name) in enumerate(editor_choices, start=1):
            print(f"  {i}. {name}")
        print(f"  {len(editor_choices) + 1}. Custom (enter manually)")

        if git_editor:
            print(f"  {len(editor_choices) + 2}. Git default editor ({git_editor})")
        else:
            print(f"  {len(editor_choices) + 2}. Git default editor")

        choice = input(f"Selection [1]: ").strip() or "1"

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(editor_choices):
                editor_cmd = editor_choices[idx - 1][0]
            elif idx == len(editor_choices) + 1:
                editor_cmd = input("Enter editor command: ").strip() or None
            else:
                # User chose "Git default editor" - use system default if available, otherwise None
                editor_cmd = git_editor
        else:
            editor_cmd = None

    cfg = Config(
        github_remote=remote,
        repo_dir=repo_dir,
        enabled_slicers=enabled,
        slicer_profile_dirs=paths,
        editor_cmd=editor_cmd,
    )
    cfg.save()
    print(f"\nSaved config to {Config.path()}")
    print(f"Repo directory: {repo_dir}")
    print("Next: run `profilesync sync`")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = Config.load()
    print(json.dumps({
        "github_remote": cfg.github_remote,
        "repo_dir": str(cfg.repo_dir),
        "enabled_slicers": cfg.enabled_slicers,
        "slicer_profile_dirs": cfg.slicer_profile_dirs,
        "editor_cmd": cfg.editor_cmd,
    }, indent=2))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    ensure_git_available()
    cfg = Config.load()
    # Check if repo is empty and initialize if needed
    clone_or_open_repo(cfg.repo_dir, cfg.github_remote)
    if not git_has_commits(cfg.repo_dir):
        initialize_empty_repo(cfg.repo_dir, cfg.github_remote)

    # 1) Fetch from GitHub first to show accurate "remote state"
    # Only pull if the remote has commits
    result = run(["git", "ls-remote", "origin", "HEAD"],
                 cwd=cfg.repo_dir, check=False)
    remote_has_commits = result.returncode == 0 and result.stdout.strip()

    if remote_has_commits:
        # Just fetch, don't pull yet - we need to export first to detect conflicts
        run(["git", "fetch", "origin"], cwd=cfg.repo_dir, check=False)
    else:
        # Remote is empty, just fetch to update remote refs
        run(["git", "fetch", "origin"], cwd=cfg.repo_dir, check=False)

    show_local_remote_summary(cfg)

    # 2) Export current slicer state to see what changed
    exported = export_from_slicers_to_repo(cfg)

    # 3) Show what changed locally
    if exported:
        by_slicer: dict[str, list[tuple[Path, Path]]] = {}
        # Group files by slicer
        for src, dst in exported:
            # Determine which slicer this file belongs to
            for slicer_key in cfg.enabled_slicers:
                slicer_root = cfg.repo_dir / REPO_PROFILES_DIR / slicer_key
                if dst.is_relative_to(slicer_root):
                    if slicer_key not in by_slicer:
                        by_slicer[slicer_key] = []
                    by_slicer[slicer_key].append((src, dst))
                    break

        print(
            f"\nFound {len(exported)} changed file(s) in your slicer folders:")
        for slicer_key, files in by_slicer.items():
            display_name = SLICER_DISPLAY_NAMES.get(
                slicer_key, slicer_key.capitalize())
            print(f"\n  {display_name} ({len(files)} files):")
            for src, dst in files[:3]:
                print(f"    • {src.name}")
            if len(files) > 3:
                print(f"    ... and {len(files) - 3} more")
    else:
        print(success(f"\n{get_check_symbol()} No local changes detected"))

    # 4) Ask what the user wants to do
    if args.action:
        action = args.action
    else:
        print("\nWhat would you like to do?")
        print(f"  {success('1)')} {highlight('Push')}: save your changes to GitHub")
        print(
            f"  {success('2)')} {highlight('Pull')}: download latest profiles from GitHub to your slicer")
        print(
            f"  {success('3)')} {highlight('Pick version')}: restore a specific saved version to your slicer")
        print(
            f"  {success('4)')} {highlight('Both')}: save to GitHub then download latest (recommended)")

        action = input("Selection [4]: ").strip() or "4"

    if action in ("1", "push"):
        return _do_push(cfg)
    elif action in ("2", "pull"):
        return _do_pull_import(cfg)
    elif action in ("3", "pick"):
        return _do_pick_version_import(cfg)
    else:  # "4" or "both"
        ret = _do_push(cfg)
        if ret != 0:
            return ret
        return _do_pull_import(cfg)


def _do_push(cfg: Config) -> int:
    # First, commit any exported changes
    committed = git_commit_if_needed(cfg.repo_dir, get_computer_id())

    # Check if remote is behind local (e.g., repo was deleted and recreated)
    needs_push = False
    if git_has_commits(cfg.repo_dir):
        # Check if remote has our commits
        result = run(["git", "rev-parse", "HEAD"],
                     cwd=cfg.repo_dir, check=False)
        if result.returncode == 0:
            local_head = result.stdout.strip()
            result = run(["git", "rev-parse", "origin/main"],
                         cwd=cfg.repo_dir, check=False)
            if result.returncode != 0:
                # Remote branch doesn't exist - need to push
                needs_push = True
            else:
                remote_head = result.stdout.strip()
                if local_head != remote_head:
                    # Local and remote are different
                    needs_push = True

    if not committed and not needs_push:
        print(
            success(f"{get_check_symbol()} Everything is already synced to GitHub."))
        return 0

    # Check if we need to sync with remote first (local and remote have diverged)
    if git_has_commits(cfg.repo_dir):
        result = run(["git", "rev-parse", "origin/main"],
                     cwd=cfg.repo_dir, check=False)
        if result.returncode == 0:
            remote_head = result.stdout.strip()
            local_head = run(["git", "rev-parse", "HEAD"],
                             cwd=cfg.repo_dir).stdout.strip()

            # Check if remote has commits we don't have
            result = run(["git", "merge-base", "--is-ancestor", local_head, remote_head],
                         cwd=cfg.repo_dir, check=False)

            if result.returncode != 0:
                # Local and remote have diverged - conflicts may occur
                print(
                    warning("\n⚠ Warning: GitHub has different profiles than your local files."))
                print(
                    "Pushing will attempt to merge your changes with GitHub's version.")
                print("This may require resolving conflicts.\n")

                if not confirm("Continue with push (may need to resolve conflicts)?", default=True):
                    print("\nPush cancelled.")
                    print(info(
                        "Tip: Use 'profilesync sync --action pull' to download GitHub's version first."))
                    return 0

    # Now try to pull with rebase to detect conflicts
    try:
        git_pull_rebase(cfg.repo_dir)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)

        if git_has_conflicts(cfg.repo_dir):
            # Real conflict - help user resolve interactively
            if not interactive_resolve_conflicts(cfg, cfg.repo_dir):
                return 1
            # After resolving, continue to push
            print(info("Pushing resolved changes to GitHub..."))
        else:
            # Some other error
            print(error(f"\nError syncing with GitHub: {error_msg}"))
            return 1

    # Now push to GitHub
    try:
        # Check if we need to set upstream (first push)
        result = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                     cwd=cfg.repo_dir, check=False)

        if result.returncode != 0:
            # No upstream set, this is likely the first push
            print(info("Setting up remote tracking..."))
            push_result = run(["git", "push", "-u", "origin",
                              "main"], cwd=cfg.repo_dir, check=False)

            # If main doesn't exist on remote, the branch is probably called master locally
            if push_result.returncode != 0:
                # Check current branch name
                branch_result = run(
                    ["git", "branch", "--show-current"], cwd=cfg.repo_dir, check=False)
                current_branch = branch_result.stdout.strip(
                ) if branch_result.returncode == 0 else "main"

                # Push with the actual current branch name
                run(["git", "push", "-u", "origin",
                    current_branch], cwd=cfg.repo_dir)
        else:
            # Normal push
            git_push(cfg.repo_dir)

        print(success(f"{get_check_symbol()} Saved to GitHub."))
        return 0
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)
        print(error(f"\nError saving to GitHub: {error_msg}"))
        return 1


def _do_pull_import(cfg: Config) -> int:
    # Ensure we are on a real branch, not detached.
    # You can make this configurable (main/master).
    try:
        git_checkout_branch(cfg.repo_dir, "main")
    except subprocess.CalledProcessError:
        # fallback
        try:
            git_checkout_branch(cfg.repo_dir, "master")
        except subprocess.CalledProcessError:
            pass

    # For pull operations, discard any local uncommitted changes
    # We want to use the remote files, not merge with local exports
    status = git_status_porcelain(cfg.repo_dir)
    if status.strip():
        # There are uncommitted changes (from export) - warn user
        print(warning(
            "\n⚠ Warning: Your local slicer files differ from the last saved version."))
        print("Pulling will overwrite your current slicer files with the version from GitHub.\n")

        if not confirm("Continue and overwrite local files with GitHub version?", default=False):
            print("\nPull cancelled. Your local files are unchanged.")
            print(info(
                "Tip: Use 'profilesync sync --action push' to save your local changes first."))
            return 0

        # User confirmed - discard local changes
        run(["git", "reset", "--hard", "HEAD"], cwd=cfg.repo_dir)

    try:
        git_pull_rebase(cfg.repo_dir)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)

        if git_has_conflicts(cfg.repo_dir):
            if not interactive_resolve_conflicts(cfg, cfg.repo_dir):
                return 1
            # Continue with import after resolving
        else:
            print(error(f"\nError downloading from GitHub: {error_msg}"))
            return 1

    imported = import_from_repo_to_slicers(cfg)
    if imported:
        # Group imports by slicer for better display
        by_slicer: dict[str, list[tuple[Path, Path]]] = {}
        for src, dst in imported:
            # Determine which slicer this file belongs to
            for slicer_key in cfg.enabled_slicers:
                slicer_dirs = [
                    Path(p) for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
                for slicer_dir in slicer_dirs:
                    if dst.is_relative_to(slicer_dir):
                        if slicer_key not in by_slicer:
                            by_slicer[slicer_key] = []
                        by_slicer[slicer_key].append((src, dst))
                        break

        print(success(
            f"{get_check_symbol()} Downloaded {len(imported)} file(s) from GitHub to your slicer folders:"))

        for slicer_key, files in by_slicer.items():
            display_name = SLICER_DISPLAY_NAMES.get(
                slicer_key, slicer_key.capitalize())
            print(f"\n  {highlight(display_name)} ({len(files)} files):")
            for src, dst in files[:3]:
                print(f"    • {dst.name}")
            if len(files) > 3:
                print(f"    ... and {len(files) - 3} more")
    else:
        print(success(f"{get_check_symbol()} All files are up to date."))
    return 0


def _do_pick_version_import(cfg: Config) -> int:
    all_commits = git_list_commits(cfg.repo_dir, limit=20)
    if not all_commits:
        print("No saved versions found.")
        return 1

    # Filter out "Initial setup" commits and parse commits
    commits_data = []
    for commit_line in all_commits:
        # Format: "hash ISO_timestamp subject"
        parts = commit_line.split(maxsplit=2)
        if len(parts) < 3:
            continue

        commit_hash = parts[0]
        timestamp_str = parts[1]
        subject = parts[2]

        # Skip "Initial setup" commits
        if "Initial setup" in subject:
            continue

        # Parse and format timestamp nicely
        try:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            formatted_time = dt.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            formatted_time = timestamp_str

        commits_data.append({
            'hash': commit_hash,
            'time': formatted_time,
            'subject': subject
        })

    if not commits_data:
        print("No saved profile versions found.")
        return 1

    print("\nSelect a saved version to restore to your slicer:")
    for i, commit in enumerate(commits_data, start=1):
        print(f"  {i}. {commit['time']}")

    raw = input("Selection: ").strip()
    if not raw.isdigit():
        print("Invalid selection.")
        return 1

    idx = int(raw)
    if idx < 1 or idx > len(commits_data):
        print("Out of range.")
        return 1

    selected = commits_data[idx - 1]
    commit_hash = selected['hash']

    # Detach and import, then return to main/master
    git_checkout_commit(cfg.repo_dir, commit_hash)
    imported = import_from_repo_to_slicers(cfg)
    if imported:
        # Group imports by slicer for better display
        by_slicer: dict[str, list[tuple[Path, Path]]] = {}
        for src, dst in imported:
            # Determine which slicer this file belongs to
            for slicer_key in cfg.enabled_slicers:
                slicer_dirs = [
                    Path(p) for p in cfg.slicer_profile_dirs.get(slicer_key, [])]
                for slicer_dir in slicer_dirs:
                    if dst.is_relative_to(slicer_dir):
                        if slicer_key not in by_slicer:
                            by_slicer[slicer_key] = []
                        by_slicer[slicer_key].append((src, dst))
                        break

        print(
            success(f"{get_check_symbol()} Restored {len(imported)} file(s) from {selected['time']}:"))

        for slicer_key, files in by_slicer.items():
            display_name = SLICER_DISPLAY_NAMES.get(
                slicer_key, slicer_key.capitalize())
            print(f"\n  {highlight(display_name)} ({len(files)} files):")
            for src, dst in files[:3]:
                print(f"    • {dst.name}")
            if len(files) > 3:
                print(f"    ... and {len(files) - 3} more")
    else:
        print(
            success(f"{get_check_symbol()} Version from {selected['time']} matches current files."))

    if confirm("Return to latest version?", default=True):
        try:
            git_checkout_branch(cfg.repo_dir, "main")
        except subprocess.CalledProcessError:
            try:
                git_checkout_branch(cfg.repo_dir, "master")
            except subprocess.CalledProcessError:
                pass
    return 0


# ---- Main CLI parser -----------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="profilesync",
        description="Sync 3D slicer profiles (Bambu Studio, OrcaSlicer) via GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              profilesync init                          # Interactive setup
              profilesync init --remote git@github.com:user/repo.git
              profilesync sync                          # Interactive sync
              profilesync sync --action push            # Push only
              profilesync sync --action pull            # Pull only
              profilesync config                        # Show current config
        """)
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize configuration and clone remote repo"
    )
    init_parser.add_argument(
        "--remote",
        help="GitHub remote URL (SSH or HTTPS)"
    )
    init_parser.add_argument(
        "--repo-dir",
        help="Local clone directory (default: auto-detect from remote)"
    )
    init_parser.add_argument(
        "--editor",
        help="Editor command for conflict resolution (default: 'code --wait')"
    )

    # sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync profiles between local slicers and GitHub"
    )
    sync_parser.add_argument(
        "--action",
        choices=["push", "pull", "pick", "both", "1", "2", "3", "4"],
        help="Sync action: push, pull, pick (version), or both"
    )

    # config command
    config_parser = subparsers.add_parser(
        "config",
        help="Show current configuration"
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "init":
            return cmd_init(args)
        elif args.command == "sync":
            return cmd_sync(args)
        elif args.command == "config":
            return cmd_config(args)
        else:
            parser.print_help()
            return 2
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"\nRun 'profilesync init' first to set up configuration.")
        return 1
    except KeyboardInterrupt:
        print("\nAborted by user.")
        return 130
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
