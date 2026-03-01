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

"""Command implementations for profilesync CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .config import Config
from .git import (
    clone_or_open_repo,
    ensure_git_available,
    git_has_commits,
    guard_not_dev_repo,
    initialize_empty_repo,
    run,
    suggest_repo_dir_from_remote,
    validate_git_remote,
)
from .slicers import get_default_slicers, Slicer
from .sync import (
    export_from_slicers_to_repo,
    rebuild_exported_from_git,
)
from .ui import (
    dim,
    error,
    get_check_symbol,
    highlight,
    info,
    success,
)


def interactive_select_slicers(slicers: list[Slicer]) -> list[str] | None:
    """Interactively select which slicers to enable. Returns None if user quits."""
    print("Select slicers to sync (comma-separated numbers):")
    for i, s in enumerate(slicers, start=1):
        print(f"  {dim(str(i) + ')')} {highlight(s.display)}")
    print(f"  {dim('Q)')} Quit")
    raw = input("Selection: ").strip()
    if not raw:
        return []
    if raw.lower() == 'q':
        return None
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
    """Configure profile directories for each enabled slicer."""
    by_key = {s.key: s for s in slicers}
    result: dict[str, list[str]] = {}
    for key in enabled:
        s = by_key[key]
        # Use the first detected directory or fallback
        default_dir = s.default_profile_dirs[0] if s.default_profile_dirs else None

        if default_dir:
            exists_marker = success(
                get_check_symbol()) if default_dir.exists() else error("X")
            print(f"\n{highlight(s.display)}: [{exists_marker}] {default_dir}")
            print(
                f"  Press {highlight('[ENTER]')} to use this directory, or enter a custom path:")
        else:
            print(f"\n{s.display}: (No directory auto-detected)")
            print("  Enter the profile directory path:")

        raw = input("> ").strip()
        if not raw:
            result[key] = [str(default_dir)] if default_dir else []
        else:
            result[key] = [raw]
    return result


# ---- Command implementations ---------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """Initialize configuration and clone remote repo."""
    ensure_git_available()

    slicers = get_default_slicers()

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

    suggested_repo_dir = suggest_repo_dir_from_remote(remote)

    if args.repo_dir:
        repo_dir = Path(args.repo_dir).expanduser()
    else:
        print(
            f"Local clone directory (press {highlight('[ENTER]')} to use default):\n  {suggested_repo_dir}")
        repo_dir_raw = input("Repo dir: ").strip()
        repo_dir = Path(repo_dir_raw).expanduser(
        ) if repo_dir_raw else suggested_repo_dir

    guard_not_dev_repo(repo_dir)

    enabled = interactive_select_slicers(slicers)
    if enabled is None:
        print(info("\nAborted. No changes were made to remote or local files."))
        return 0
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
            print(f"  {dim(str(i) + ')')} {name}")
        print(f"  {dim(str(len(editor_choices) + 1) + ')')} Custom (enter manually)")

        if git_editor:
            print(
                f"  {dim(str(len(editor_choices) + 2) + ')')} Git default editor ({git_editor})")
        else:
            print(f"  {dim(str(len(editor_choices) + 2) + ')')} Git default editor")
        print(f"  {dim('Q)')} Quit")

        choice = input(f"Selection [1]: ").strip() or "1"

        if choice.lower() == 'q':
            print(info("\nAborted. No changes were made to remote or local files."))
            return 0
        elif choice.isdigit():
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
    """Show current configuration."""
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
    """Main sync command - launches the interactive TUI."""
    ensure_git_available()
    cfg = Config.load()
    # Check if repo is empty and initialize if needed
    clone_or_open_repo(cfg.repo_dir, cfg.github_remote)
    if not git_has_commits(cfg.repo_dir):
        initialize_empty_repo(cfg.repo_dir, cfg.github_remote)

    # 1) Fetch from server first to show accurate "remote state"
    result = run(["git", "ls-remote", "origin", "HEAD"],
                 cwd=cfg.repo_dir, check=False)
    remote_has_commits = result.returncode == 0 and result.stdout.strip()

    run(["git", "fetch", "origin"], cwd=cfg.repo_dir, check=False)

    # 2) Export current slicer state to see what changed
    exported = export_from_slicers_to_repo(cfg)
    # If nothing new was exported, check for uncommitted changes from a previous run
    if not exported:
        exported = rebuild_exported_from_git(cfg)

    # 3) Launch the interactive TUI
    from .tui import SyncApp, build_status_text
    status_text = build_status_text(
        cfg, exported, bool(remote_has_commits))
    app = SyncApp(
        cfg=cfg, exported=exported, status_text=status_text)
    result_code = app.run()
    return result_code if isinstance(result_code, int) else 0
