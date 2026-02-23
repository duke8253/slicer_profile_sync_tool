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
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import Config
from .git import (
    clone_or_open_repo,
    ensure_git_available,
    get_computer_id,
    git_checkout_branch,
    git_checkout_commit,
    git_commit_if_needed,
    git_get_conflicted_files,
    git_has_commits,
    git_has_conflicts,
    git_list_commits,
    git_pull_rebase,
    git_push,
    git_remote_has_profiles,
    git_status_porcelain,
    guard_not_dev_repo,
    initialize_empty_repo,
    REPO_PROFILES_DIR,
    run,
    suggest_repo_dir_from_remote,
    validate_git_remote,
)
from .slicers import get_default_slicers, Slicer
from .sync import (
    display_grouped_files,
    export_from_slicers_to_repo,
    group_by_slicer_and_type,
    import_from_repo_to_slicers,
    SLICER_DISPLAY_NAMES,
)
from .ui import (
    confirm,
    dim,
    error,
    get_check_symbol,
    highlight,
    info,
    success,
    warning,
)


# ---- Interactive UI flows ------------------------------------------------------

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


def show_local_remote_summary(cfg: Config) -> None:
    """Show sync status summary."""
    print(f"\n{highlight('Sync status:')}")
    print(f"  {dim('Local folder:')} {cfg.repo_dir}")
    print(f"  {dim('Remote server:')} {cfg.github_remote}")

    # Fetch latest from remote to ensure we have up-to-date info
    # Use --prune to remove stale remote-tracking branches
    run(["git", "fetch", "origin", "--prune"], cwd=cfg.repo_dir, check=False)

    # Check if profiles actually exist on server (origin/main)
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
                print(f"  {dim('Last sync:')} {info(time_str)}")
            except Exception:
                print(f"  {dim('Last sync:')} {out}")
        else:
            print(f"  {dim('Last sync:')} Unknown")
    else:
        print(f"  {dim('Last sync:')} Never (no profiles on server yet)")

    # Show a quick file inventory grouped by type from sync directory
    for slicer in cfg.enabled_slicers:
        root = cfg.repo_dir / REPO_PROFILES_DIR / slicer
        if not root.exists():
            continue

        # Group files by type
        files_by_type: dict[str, list[Path]] = {}
        for json_file in root.rglob("*.json"):
            try:
                rel = json_file.relative_to(root)
                profile_type = rel.parts[0] if rel.parts else "other"
                profile_type = profile_type.capitalize()
            except (ValueError, IndexError):
                profile_type = "Other"

            if profile_type not in files_by_type:
                files_by_type[profile_type] = []
            files_by_type[profile_type].append(json_file)

        if not files_by_type:
            continue

        display_name = SLICER_DISPLAY_NAMES.get(slicer, slicer.capitalize())
        total = sum(len(files) for files in files_by_type.values())
        print(f"\n  {highlight(display_name)} ({total} files):")

        for profile_type, files in sorted(files_by_type.items()):
            print(f"    {info(profile_type)}: {len(files)}")


def open_editor(cfg: Config, path: Path) -> None:
    """Open a file in the configured editor."""
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
    """Main sync command - interactive sync workflow."""
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

    # ─── Interactive TUI mode ───────────────────────────────────────────
    if not args.action:
        from .tui import SyncApp, build_status_text
        status_text = build_status_text(
            cfg, exported, bool(remote_has_commits))
        app = SyncApp(
            cfg=cfg, exported=exported, status_text=status_text)
        result_code = app.run()
        return result_code if isinstance(result_code, int) else 0

    # ─── Non-interactive (--action) mode ────────────────────────────────

    # 3) Show what changed locally
    if exported:
        grouped = group_by_slicer_and_type(exported, cfg, cfg.repo_dir)
        display_grouped_files(
            grouped, "\nFound {count} changed file(s) in your slicer folders:")
    else:
        print(
            success(f"\n{get_check_symbol()} Your slicer folders match the sync folder"))

    # Check sync folder status
    git_status = git_status_porcelain(cfg.repo_dir)
    has_unsaved_changes = bool(git_status.strip())

    # Check sync folder vs remote
    if remote_has_commits:
        local_head = run(["git", "rev-parse", "HEAD"],
                         cwd=cfg.repo_dir, check=False).stdout.strip()
        remote_head = run(["git", "rev-parse", "origin/main"],
                          cwd=cfg.repo_dir, check=False).stdout.strip()

        if has_unsaved_changes:
            # If there are unsaved changes, we can't match server
            print(info("\n* Sync folder has changes not yet saved to server"))

            # Show which files are changed
            changed_files = []
            for line in git_status.strip().split('\n'):
                if not line.strip():
                    continue
                # Git status --porcelain format: XY filename
                # We care about files in profiles/ directory
                parts = line.split(maxsplit=1)
                if len(parts) >= 2:
                    filepath = parts[1].strip()
                    # Remove quotes if present
                    if filepath.startswith('"') and filepath.endswith('"'):
                        filepath = filepath[1:-1]
                    file_path = cfg.repo_dir / filepath
                    if file_path.exists() and str(REPO_PROFILES_DIR) in filepath:
                        # Create a dummy tuple for grouping (we only have dst in this case)
                        changed_files.append((file_path, file_path))

            if changed_files:
                grouped = group_by_slicer_and_type(
                    changed_files, cfg, cfg.repo_dir, use_dst_for_type=True)
                display_grouped_files(grouped, "  {count} changed file(s):")
        elif local_head == remote_head:
            # No unsaved changes and HEAD matches remote
            print(success(f"{get_check_symbol()} Sync folder matches server"))
        else:
            # Check if local is ahead, behind, or diverged
            result = run(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
                         cwd=cfg.repo_dir, check=False)
            if result.returncode == 0:
                ahead, behind = result.stdout.strip().split()
                if int(ahead) > 0 and int(behind) > 0:
                    print(warning(
                        f"⚠ Sync folder differs from server (out of sync)"))
                elif int(ahead) > 0:
                    print(
                        info(f"↑ Sync folder has {ahead} save(s) not yet on server"))
                elif int(behind) > 0:
                    print(
                        info(f"↓ Server has {behind} newer save(s)"))
    else:
        if has_unsaved_changes:
            print(info("\n* Sync folder has changes not yet saved to server"))

            # Show which files are changed
            changed_files = []
            for line in git_status.strip().split('\n'):
                if not line.strip():
                    continue
                # Git status --porcelain format: XY filename
                parts = line.split(maxsplit=1)
                if len(parts) >= 2:
                    filepath = parts[1].strip()
                    # Remove quotes if present
                    if filepath.startswith('"') and filepath.endswith('"'):
                        filepath = filepath[1:-1]
                    file_path = cfg.repo_dir / filepath
                    if file_path.exists() and str(REPO_PROFILES_DIR) in filepath:
                        changed_files.append((file_path, file_path))

            if changed_files:
                grouped = group_by_slicer_and_type(
                    changed_files, cfg, cfg.repo_dir, use_dst_for_type=True)
                display_grouped_files(grouped, "  {count} changed file(s):")
        else:
            print(dim("ℹ No profiles saved to server yet"))

    # 4) Ask what the user wants to do
    if args.action:
        action = args.action
    else:
        print("\nWhat would you like to do?")
        print(f"  {dim('1)')} {highlight('Push')}: save your changes to server")
        print(
            f"  {dim('2)')} {highlight('Pull')}: download latest profiles from server to your slicer")
        print(
            f"  {dim('3)')} {highlight('Pick version')}: restore a specific saved version to your slicer")
        print(
            f"  {dim('4)')} {highlight('Full sync')}: save to server then download latest (recommended)")
        print(f"  {dim('Q)')} Quit")

        action = input("Selection [4]: ").strip() or "4"

    if action.lower() == 'q':
        print(info("\nAborted. No changes were made to remote or local files."))
        return 0
    elif action in ("1", "push"):
        return do_push(cfg, exported=exported)
    elif action in ("2", "pull"):
        return do_pull_import(cfg)
    elif action in ("3", "pick"):
        return do_pick_version_import(cfg)
    else:  # "4" or "full sync"
        ret = do_push(cfg, exported=exported)
        if ret != 0:
            return ret
        return do_pull_import(cfg)


def do_push(cfg: Config, exported: list[tuple[Path, Path]] | None = None) -> int:
    """Push all exported changes to server (non-interactive / --action mode)."""

    # --- Commit and push ----------------------------------------------------
    committed = git_commit_if_needed(cfg.repo_dir, get_computer_id())

    # Check if remote is behind local
    needs_push = False
    if git_has_commits(cfg.repo_dir):
        result = run(["git", "rev-parse", "HEAD"],
                     cwd=cfg.repo_dir, check=False)
        if result.returncode == 0:
            local_head = result.stdout.strip()
            result = run(["git", "rev-parse", "origin/main"],
                         cwd=cfg.repo_dir, check=False)
            if result.returncode != 0:
                needs_push = True
            else:
                remote_head = result.stdout.strip()
                if local_head != remote_head:
                    needs_push = True

    if not committed and not needs_push:
        print(
            success(f"{get_check_symbol()} Everything is already synced to server."))
        return 0

    # Check for divergence
    if git_has_commits(cfg.repo_dir):
        result = run(["git", "rev-parse", "origin/main"],
                     cwd=cfg.repo_dir, check=False)
        if result.returncode == 0:
            remote_head = result.stdout.strip()
            local_head = run(["git", "rev-parse", "HEAD"],
                             cwd=cfg.repo_dir).stdout.strip()

            result = run(["git", "merge-base", "--is-ancestor", remote_head, local_head],
                         cwd=cfg.repo_dir, check=False)

            if result.returncode != 0:
                print(
                    warning("\n⚠ Warning: Server has different profiles than your local files."))
                print(
                    "Pushing will attempt to merge your changes with the server's version.")
                print("This may require resolving conflicts.\n")

                if not confirm("Continue with push (may need to resolve conflicts)?", default=True):
                    print("\nPush cancelled.")
                    print(info(
                        "Tip: Use 'profilesync sync --action pull' to download server's version first."))
                    return 0

    # Pull with rebase to detect conflicts
    try:
        git_pull_rebase(cfg.repo_dir)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)

        if git_has_conflicts(cfg.repo_dir):
            if not interactive_resolve_conflicts(cfg, cfg.repo_dir):
                return 1
            print(info("Pushing resolved changes to server..."))
        else:
            print(error(f"\nError syncing with server: {error_msg}"))
            return 1

    # Push to server
    try:
        result = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                     cwd=cfg.repo_dir, check=False)

        if result.returncode != 0:
            print(info("Setting up remote tracking..."))
            push_result = run(["git", "push", "-u", "origin",
                              "main"], cwd=cfg.repo_dir, check=False)

            if push_result.returncode != 0:
                branch_result = run(
                    ["git", "branch", "--show-current"], cwd=cfg.repo_dir, check=False)
                current_branch = branch_result.stdout.strip(
                ) if branch_result.returncode == 0 else "main"

                run(["git", "push", "-u", "origin",
                    current_branch], cwd=cfg.repo_dir)
        else:
            git_push(cfg.repo_dir)

        print(success(f"{get_check_symbol()} Saved to server."))
        return 0
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)
        print(error(f"\nError saving to server: {error_msg}"))
        return 1


def do_pull_import(cfg: Config) -> int:
    """Pull from server and import all profiles (non-interactive / --action mode)."""

    # Ensure we are on a real branch, not detached.
    try:
        git_checkout_branch(cfg.repo_dir, "main")
    except subprocess.CalledProcessError:
        try:
            git_checkout_branch(cfg.repo_dir, "master")
        except subprocess.CalledProcessError:
            pass

    # For pull operations, discard any local uncommitted changes
    status = git_status_porcelain(cfg.repo_dir)
    if status.strip():
        print(warning(
            "\n⚠ Warning: Your local slicer files differ from the last saved version."))
        print("Pulling will overwrite your current slicer files with the version from server.\n")

        if not confirm("Continue and overwrite local files with server version?", default=False):
            print("\nPull cancelled. Your local files are unchanged.")
            print(info(
                "Tip: Use 'profilesync sync --action push' to save your local changes first."))
            return 0

        run(["git", "reset", "--hard", "HEAD"], cwd=cfg.repo_dir)
        run(["git", "clean", "-fd"], cwd=cfg.repo_dir)

    try:
        git_pull_rebase(cfg.repo_dir)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or str(e)

        if git_has_conflicts(cfg.repo_dir):
            if not interactive_resolve_conflicts(cfg, cfg.repo_dir):
                return 1
        else:
            print(error(f"\nError downloading from server: {error_msg}"))
            return 1

    # Import ALL profiles (no file selection in --action mode)
    imported = import_from_repo_to_slicers(cfg)
    if imported:
        grouped = group_by_slicer_and_type(
            imported, cfg, cfg.repo_dir, use_dst_for_type=False)
        display_grouped_files(grouped, success(
            f"{get_check_symbol()} Downloaded {{count}} file(s) from server to your slicer folders:"))
    else:
        print(
            success(f"{get_check_symbol()} All profiles are already up to date."))
    return 0


def do_pick_version_import(cfg: Config) -> int:
    """Pick a specific version from history and import to slicers."""
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
        print(f"  {dim(str(i) + ')')} {commit['time']}")
    print(f"  {dim('Q)')} Quit")

    raw = input("Selection: ").strip()
    if raw.lower() == 'q':
        print(info("\nAborted. No changes were made to remote or local files."))
        return 0
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
        grouped = group_by_slicer_and_type(
            imported, cfg, cfg.repo_dir, use_dst_for_type=False)
        display_grouped_files(grouped, success(
            f"{get_check_symbol()} Restored {{count}} file(s) from {selected['time']}:"))
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
