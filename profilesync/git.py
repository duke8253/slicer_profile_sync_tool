"""Git operations and repository management."""

from __future__ import annotations

import hashlib
import os
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import Config, DEFAULT_DATA_DIR
from .ui import dim, get_check_symbol, info, success


# ---- Repository constants ------------------------------------------------------
REPO_PROFILES_DIR = Path("profiles")


# ---- Git utilities -------------------------------------------------------------

def run(cmd: list[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Execute a command."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def ensure_git_available() -> None:
    """Check if git is installed."""
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
    """Clone repository or ensure existing one has correct remote."""
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
    """Pull from remote with rebase."""
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
    """Initialize a new repository with basic structure."""
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
    """Get git status in machine-readable format."""
    return run(["git", "status", "--porcelain"], cwd=repo_dir).stdout


def git_head_info(repo_dir: Path) -> str:
    """Return short commit info: shortsha date subject."""
    out = run(["git", "log", "-1", "--pretty=format:%h %cI %s"],
              cwd=repo_dir, check=False).stdout.strip()
    return out or "(no commits)"


def git_commit_if_needed(repo_dir: Path, message: str) -> bool:
    """Commit changes if there are any. Returns True if committed."""
    run(["git", "add", "-A"], cwd=repo_dir)
    st = git_status_porcelain(repo_dir)
    if not st.strip():
        return False
    run(["git", "commit", "-m", message], cwd=repo_dir)
    return True


def git_push(repo_dir: Path) -> None:
    """Push to remote."""
    run(["git", "push"], cwd=repo_dir)


def git_list_commits(repo_dir: Path, limit: int = 20) -> list[str]:
    """List recent commits."""
    out = run(["git", "log", f"-{limit}", "--pretty=format:%h %cI %s"],
              cwd=repo_dir, check=False).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def git_checkout_commit(repo_dir: Path, commit: str) -> None:
    """Checkout specific commit (detached HEAD)."""
    run(["git", "checkout", commit], cwd=repo_dir)


def git_checkout_branch(repo_dir: Path, branch: str = "main") -> None:
    """Checkout a branch."""
    run(["git", "checkout", branch], cwd=repo_dir)


def now_iso() -> str:
    """Get current time in ISO format."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def get_computer_id() -> str:
    """
    Get a human-readable identifier for this computer.
    Returns something like "Synced from macOS (duke@MacBook-Pro)" or "Synced from Windows (john@DESKTOP-ABC123)"
    """
    import platform
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


# ---- Repository safety guards --------------------------------------------------

def suggest_repo_dir_from_remote(remote: str) -> Path:
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


def is_inside(child: Path, parent: Path) -> bool:
    """Check if child path is inside parent path."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def find_git_root(start: Path) -> Optional[Path]:
    """Find the git repository root starting from a path."""
    cur = start.resolve()
    for _ in range(50):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def guard_not_dev_repo(repo_dir: Path) -> None:
    """
    Prevent users from accidentally pointing the sync repo at the same repo
    they're developing profilesync within.
    Allow data/ subdirectory for portable local storage.
    """
    dev_root = find_git_root(Path(__file__).parent)
    if not dev_root:
        return

    # Allow if repo_dir is under the designated data directory
    if is_inside(repo_dir, DEFAULT_DATA_DIR):
        return

    # Block if trying to use dev repo root or would conflict with .git
    if is_inside(repo_dir, dev_root):
        raise RuntimeError(
            f"Refusing to use repo_dir inside the profilesync dev repo.\n"
            f"  dev repo: {dev_root}\n"
            f"  repo_dir: {repo_dir}\n"
            f"Use the default data/ directory or choose a location outside this repo."
        )


def sha256_file(path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
