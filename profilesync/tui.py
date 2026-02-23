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

"""Textual TUI for the profilesync sync workflow."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from .config import Config
from .git import (
    get_computer_id,
    git_checkout_branch,
    git_checkout_commit,
    git_commit_if_needed,
    git_has_commits,
    git_has_conflicts,
    git_list_commits,
    git_pull_rebase,
    git_push,
    git_status_porcelain,
    REPO_PROFILES_DIR,
    run,
)
from .sync import (
    collect_server_profiles,
    export_selected_to_repo,
    import_from_repo_to_slicers,
    import_selected_profiles,
    SLICER_DISPLAY_NAMES,
)


# ── Status builder ──────────────────────────────────────────────────────────


def build_status_text(
    cfg: Config,
    exported: list[tuple[Path, Path]],
    remote_has_commits: bool,
) -> Text:
    """Build Rich Text status summary for the TUI status panel."""
    text = Text()

    text.append("  Local folder: ", style="dim")
    text.append(str(cfg.repo_dir) + "\n")
    text.append("  Remote: ", style="dim")
    text.append(cfg.github_remote + "\n")

    # Last sync time
    if remote_has_commits:
        result = run(
            ["git", "log", "-1", "--pretty=format:%cI",
             "origin/main", "--", str(REPO_PROFILES_DIR)],
            cwd=cfg.repo_dir, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                dt = datetime.fromisoformat(
                    result.stdout.strip().replace("Z", "+00:00"))
                time_str = dt.strftime("%B %d, %Y at %I:%M %p")
                text.append("  Last sync: ", style="dim")
                text.append(time_str + "\n", style="magenta")
            except Exception:
                pass

    # File inventory by slicer / type
    for slicer in cfg.enabled_slicers:
        root = cfg.repo_dir / REPO_PROFILES_DIR / slicer
        if not root.exists():
            continue

        files_by_type: dict[str, int] = {}
        for json_file in root.rglob("*.json"):
            try:
                rel = json_file.relative_to(root)
                profile_type = (
                    rel.parts[0].capitalize() if rel.parts else "Other")
            except (ValueError, IndexError):
                profile_type = "Other"
            files_by_type[profile_type] = files_by_type.get(
                profile_type, 0) + 1

        if not files_by_type:
            continue

        display_name = SLICER_DISPLAY_NAMES.get(
            slicer, slicer.capitalize())
        total = sum(files_by_type.values())
        text.append(f"\n  {display_name}", style="bold")
        text.append(f" ({total} files)\n")
        for ptype, count in sorted(files_by_type.items()):
            text.append(f"    {ptype}: ", style="magenta")
            text.append(f"{count}  ")
        text.append("\n")

    text.append("\n")

    # Changes detected
    if exported:
        additions = sum(1 for src, _ in exported if src is not None)
        deletions = sum(1 for src, _ in exported if src is None)
        parts: list[str] = []
        if additions:
            parts.append(f"{additions} changed")
        if deletions:
            parts.append(f"{deletions} deleted")
        text.append(
            f"  ● Found {' + '.join(parts)} file(s) in slicer folders\n",
            style="yellow",
        )
    else:
        text.append(
            "  ✓ Your slicer folders match the sync folder\n",
            style="green",
        )

    # Sync status vs server
    git_status = git_status_porcelain(cfg.repo_dir)
    has_unsaved = bool(git_status.strip())

    if remote_has_commits:
        local_head = run(
            ["git", "rev-parse", "HEAD"],
            cwd=cfg.repo_dir, check=False,
        ).stdout.strip()
        remote_head = run(
            ["git", "rev-parse", "origin/main"],
            cwd=cfg.repo_dir, check=False,
        ).stdout.strip()

        if has_unsaved:
            text.append(
                "  ● Sync folder has unsaved changes\n", style="yellow")
        elif local_head == remote_head:
            text.append(
                "  ✓ Sync folder matches server\n", style="green")
        else:
            result = run(
                ["git", "rev-list", "--left-right", "--count",
                 "HEAD...origin/main"],
                cwd=cfg.repo_dir, check=False,
            )
            if result.returncode == 0:
                ahead_s, behind_s = result.stdout.strip().split()
                ahead, behind = int(ahead_s), int(behind_s)
                if ahead > 0 and behind > 0:
                    text.append(
                        "  ⚠ Sync folder differs from server\n",
                        style="yellow")
                elif ahead > 0:
                    text.append(
                        f"  ↑ {ahead} save(s) not yet on server\n",
                        style="cyan")
                elif behind > 0:
                    text.append(
                        f"  ↓ {behind} newer save(s) on server\n",
                        style="cyan")
    else:
        if has_unsaved:
            text.append(
                "  ● Sync folder has unsaved changes\n", style="yellow")
        else:
            text.append(
                "  ℹ No profiles saved to server yet\n", style="dim")

    return text


# ── Textual App ─────────────────────────────────────────────────────────────


class SyncApp(App[int]):
    """ProfileSync interactive sync TUI."""

    TITLE = "ProfileSync"

    CSS = """
    Screen { layout: vertical; }

    #status-panel {
        height: auto;
        max-height: 50%;
        padding: 1 2;
        border-bottom: heavy $primary;
        overflow-y: auto;
    }

    #menu {
        height: 1fr;
        margin: 1 2;
    }

    #screen-title {
        padding: 1 2;
    }

    #file-list, #version-list {
        height: 1fr;
        margin: 0 2;
    }

    #select-status {
        padding: 0 2;
        color: $text-muted;
    }

    #loading {
        padding: 1 2;
        text-style: italic;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        cfg: Config,
        exported: list[tuple[Path, Path]],
        status_text: Text,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.exported = exported
        self.status_text = status_text

    def on_mount(self) -> None:
        self.push_screen(MainScreen())

    def refresh_status(self) -> None:
        """Rebuild status text on the app (MainScreen picks it up on resume)."""
        result = run(
            ["git", "ls-remote", "origin", "HEAD"],
            cwd=self.cfg.repo_dir, check=False,
        )
        remote_has_commits = (
            result.returncode == 0 and bool(result.stdout.strip()))
        self.status_text = build_status_text(
            self.cfg, self.exported, remote_has_commits)


# ── Main Screen ─────────────────────────────────────────────────────────────


class MainScreen(Screen):
    """Displays sync status and action menu."""

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
    ]

    def on_screen_resume(self) -> None:
        """Refresh the status panel every time this screen becomes active."""
        self.query_one("#status-panel", Static).update(
            self.app.status_text)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.app.status_text, id="status-panel")
        yield OptionList(
            Option(
                Text.from_markup(
                    "[bold]Push[/bold] — save your changes to server"),
                id="push"),
            Option(
                Text.from_markup(
                    "[bold]Pull[/bold] — download latest profiles"
                    " from server"),
                id="pull"),
            Option(
                Text.from_markup(
                    "[bold]Full Sync[/bold] — push then pull"
                    " [dim](recommended)[/dim]"),
                id="full_sync"),
            Option(
                Text.from_markup(
                    "[bold]Pick Version[/bold]"
                    " — restore a specific saved version"),
                id="pick"),
            id="menu",
        )
        yield Footer()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option_id
        if oid == "push":
            if self.app.exported:
                self.app.push_screen(PushScreen())
            else:
                self.notify(
                    "Nothing to push — slicer folders match sync folder",
                    severity="information")
        elif oid == "pull":
            self.app.push_screen(PullScreen())
        elif oid == "full_sync":
            if self.app.exported:
                self.app.push_screen(PushScreen(then_pull=True))
            else:
                # Nothing to push, go directly to pull
                self.app.push_screen(PullScreen())
        elif oid == "pick":
            self.app.push_screen(PickVersionScreen())

    def action_quit_app(self) -> None:
        self.app.exit(0)


# ── Push Screen ─────────────────────────────────────────────────────────────


class PushScreen(Screen):
    """Select files to push, then execute push in a worker."""

    BINDINGS = [
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("i", "invert", "Invert"),
        Binding("s", "range_select", "Range"),
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "go_back", "Back"),
    ]

    def __init__(self, then_pull: bool = False) -> None:
        super().__init__()
        self.then_pull = then_pull
        self._range_anchor: int | None = None

    def compose(self) -> ComposeResult:
        title = Text()
        title.append("Select files to push to server", style="bold")
        title.append("  (all selected by default)", style="dim")
        yield Static(title, id="screen-title")

        # Use integer indices as values (must be hashable)
        selections: list[tuple[Text, int, bool]] = []
        for i, (src, dst) in enumerate(self.app.exported):
            selections.append((self._make_label(src, dst), i, True))

        yield SelectionList[int](*selections, id="file-list")
        yield Static("", id="select-status")
        yield Footer()

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_label(self, src: Path | None, dst: Path) -> Text:
        cfg = self.app.cfg
        try:
            rel = dst.relative_to(cfg.repo_dir / REPO_PROFILES_DIR)
            slicer_key = rel.parts[0]
            display_name = SLICER_DISPLAY_NAMES.get(
                slicer_key, slicer_key.capitalize())
            rel_slicer = dst.relative_to(
                cfg.repo_dir / REPO_PROFILES_DIR / slicer_key)
            ptype = (rel_slicer.parts[0].capitalize()
                     if rel_slicer.parts else "Other")
        except (ValueError, IndexError):
            display_name = "Unknown"
            ptype = "Other"

        label = Text()
        label.append(f"[{display_name}] ", style="bold")
        label.append(f"{ptype} / ", style="magenta")
        if src is None:
            label.append(dst.name, style="red")
            label.append(" [DELETE]", style="bold red")
        else:
            label.append(dst.name)
        return label

    # ── events ───────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._update_status()

    def on_selection_list_selected_changed(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        sl = self.query_one(SelectionList)
        self.query_one("#select-status", Static).update(
            f"  {len(sl.selected)} of {sl.option_count} selected")

    # ── actions ──────────────────────────────────────────────────────────

    def action_select_all(self) -> None:
        self.query_one(SelectionList).select_all()

    def action_select_none(self) -> None:
        self.query_one(SelectionList).deselect_all()

    def action_invert(self) -> None:
        self.query_one(SelectionList).toggle_all()

    def action_range_select(self) -> None:
        sl = self.query_one(SelectionList)
        current = sl.highlighted
        if current is None:
            return
        if self._range_anchor is None:
            self._range_anchor = current
            self.notify(
                f"Range anchor set (item {current + 1})",
                severity="information")
        else:
            lo = min(self._range_anchor, current)
            hi = max(self._range_anchor, current)
            for idx in range(lo, hi + 1):
                sl.select(idx)
            self._range_anchor = None
            self.notify(
                f"Selected items {lo + 1}–{hi + 1}",
                severity="information")

    def action_confirm(self) -> None:
        selected = list(self.query_one(SelectionList).selected)
        if not selected:
            self.notify("No files selected", severity="warning")
            return
        self._execute_push(selected)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # ── worker ───────────────────────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _execute_push(self, selected_indices: list[int]) -> None:
        cfg = self.app.cfg
        exported = self.app.exported
        selected_files = [exported[i] for i in selected_indices]

        # Revert and re-export if only a subset was selected
        if len(selected_files) < len(exported):
            if git_has_commits(cfg.repo_dir):
                run(["git", "checkout", "HEAD", "--", "."],
                    cwd=cfg.repo_dir, check=False)
            run(["git", "clean", "-fd"], cwd=cfg.repo_dir, check=False)
            export_selected_to_repo(cfg, selected_files)

        # Commit
        git_commit_if_needed(cfg.repo_dir, get_computer_id())

        # Check if push is needed
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
                elif local_head != result.stdout.strip():
                    needs_push = True

        if not needs_push:
            self.app.call_from_thread(
                self.notify, "✓ Already synced to server",
                severity="information")
            self.app.call_from_thread(self._after_push, True)
            return

        # Pull-rebase before pushing
        try:
            git_pull_rebase(cfg.repo_dir)
        except subprocess.CalledProcessError:
            if git_has_conflicts(cfg.repo_dir):
                run(["git", "rebase", "--abort"],
                    cwd=cfg.repo_dir, check=False)
                self.app.call_from_thread(
                    self.notify,
                    "Conflicts detected — resolve manually and retry",
                    severity="error", timeout=10)
            else:
                self.app.call_from_thread(
                    self.notify, "Error syncing with server",
                    severity="error", timeout=10)
            self.app.call_from_thread(self._after_push, False)
            return

        # Push
        try:
            result = run(
                ["git", "rev-parse", "--abbrev-ref",
                 "--symbolic-full-name", "@{u}"],
                cwd=cfg.repo_dir, check=False)
            if result.returncode != 0:
                push_result = run(
                    ["git", "push", "-u", "origin", "main"],
                    cwd=cfg.repo_dir, check=False)
                if push_result.returncode != 0:
                    branch = run(
                        ["git", "branch", "--show-current"],
                        cwd=cfg.repo_dir, check=False,
                    ).stdout.strip() or "main"
                    run(["git", "push", "-u", "origin", branch],
                        cwd=cfg.repo_dir)
            else:
                git_push(cfg.repo_dir)

            n = len(selected_indices)
            self.app.call_from_thread(
                self.notify, f"✓ Pushed {n} file(s) to server",
                severity="information")
            self.app.call_from_thread(self._after_push, True)
        except subprocess.CalledProcessError:
            self.app.call_from_thread(
                self.notify, "Push failed", severity="error", timeout=10)
            self.app.call_from_thread(self._after_push, False)

    def _after_push(self, success: bool) -> None:
        if success:
            self.app.exported = []
            self.app.refresh_status()
        self.app.pop_screen()
        if success and self.then_pull:
            self.app.push_screen(PullScreen())


# ── Pull Screen ─────────────────────────────────────────────────────────────


class PullScreen(Screen):
    """Load server profiles, let user select, and import."""

    BINDINGS = [
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("i", "invert", "Invert"),
        Binding("s", "range_select", "Range"),
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "go_back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._profiles: list[dict] = []
        self._range_anchor: int | None = None

    def compose(self) -> ComposeResult:
        title = Text()
        title.append("Download profiles from server", style="bold")
        title.append(
            "  (new & changed selected by default)", style="dim")
        yield Static(title, id="screen-title")
        yield Static("  Loading profiles from server...", id="loading")
        yield Footer()

    def on_mount(self) -> None:
        self._load_profiles()

    @work(thread=True)
    def _load_profiles(self) -> None:
        cfg = self.app.cfg

        # Ensure on main branch
        try:
            git_checkout_branch(cfg.repo_dir, "main")
        except subprocess.CalledProcessError:
            try:
                git_checkout_branch(cfg.repo_dir, "master")
            except subprocess.CalledProcessError:
                pass

        # Discard uncommitted changes (from earlier export step)
        status = git_status_porcelain(cfg.repo_dir)
        if status.strip():
            run(["git", "reset", "--hard", "HEAD"], cwd=cfg.repo_dir)
            run(["git", "clean", "-fd"], cwd=cfg.repo_dir)

        # Pull latest from server
        try:
            git_pull_rebase(cfg.repo_dir)
        except subprocess.CalledProcessError:
            if git_has_conflicts(cfg.repo_dir):
                run(["git", "rebase", "--abort"],
                    cwd=cfg.repo_dir, check=False)
            self.app.call_from_thread(
                self.notify,
                "Error downloading from server",
                severity="error", timeout=10)
            self.app.call_from_thread(self.app.pop_screen)
            return

        profiles = collect_server_profiles(cfg)
        self.app.call_from_thread(self._display_profiles, profiles)

    def _display_profiles(self, profiles: list[dict]) -> None:
        self._profiles = profiles

        # Remove loading message
        try:
            self.query_one("#loading").remove()
        except Exception:
            pass

        if not profiles:
            self.notify(
                "No profiles found on server", severity="information")
            self.app.pop_screen()
            return

        # Build selections — use integer index as value (hashable)
        selections: list[tuple[Text, int, bool]] = []
        for i, p in enumerate(profiles):
            display_name = SLICER_DISPLAY_NAMES.get(
                p["slicer_key"], p["slicer_key"].capitalize())

            label = Text()
            label.append(f"[{display_name}] ", style="bold")
            label.append(
                f"{p['profile_type']} / ", style="magenta")
            label.append(p["filename"])

            if p["matches_local"]:
                label.append("  (matches local)", style="dim")
                checked = False
            elif p["local_path"] and p["local_path"].exists():
                label.append(
                    "  (differs from local)", style="yellow")
                checked = True
            else:
                label.append("  (new)", style="green")
                checked = True

            selections.append((label, i, checked))

        # Mount widgets dynamically
        footer = self.query_one(Footer)
        self.mount(
            SelectionList[int](*selections, id="file-list"),
            before=footer)
        self.mount(
            Static("", id="select-status"),
            before=footer)

        self.query_one("#file-list").focus()
        self._update_status()

    def on_selection_list_selected_changed(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        try:
            sl = self.query_one(SelectionList)
            self.query_one("#select-status", Static).update(
                f"  {len(sl.selected)} of {sl.option_count} selected")
        except Exception:
            pass

    def action_select_all(self) -> None:
        try:
            self.query_one(SelectionList).select_all()
        except Exception:
            pass

    def action_select_none(self) -> None:
        try:
            self.query_one(SelectionList).deselect_all()
        except Exception:
            pass

    def action_invert(self) -> None:
        try:
            self.query_one(SelectionList).toggle_all()
        except Exception:
            pass

    def action_range_select(self) -> None:
        try:
            sl = self.query_one(SelectionList)
        except Exception:
            return
        current = sl.highlighted
        if current is None:
            return
        if self._range_anchor is None:
            self._range_anchor = current
            self.notify(
                f"Range anchor set (item {current + 1})",
                severity="information")
        else:
            lo = min(self._range_anchor, current)
            hi = max(self._range_anchor, current)
            for idx in range(lo, hi + 1):
                sl.select(idx)
            self._range_anchor = None
            self.notify(
                f"Selected items {lo + 1}–{hi + 1}",
                severity="information")

    def action_confirm(self) -> None:
        try:
            selected = list(self.query_one(SelectionList).selected)
        except Exception:
            return
        if not selected:
            self.notify("No profiles selected", severity="warning")
            return
        self._execute_pull(selected)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @work(thread=True, exclusive=True)
    def _execute_pull(self, selected_indices: list[int]) -> None:
        selected = [self._profiles[i] for i in selected_indices]
        imported = import_selected_profiles(self.app.cfg, selected)

        n = len(imported)
        if n > 0:
            self.app.call_from_thread(
                self.notify,
                f"✓ Downloaded {n} file(s) to slicer folders",
                severity="information")
        else:
            self.app.call_from_thread(
                self.notify,
                "All selected files are already up to date",
                severity="information")
        # Refresh main screen status after pull
        self.app.call_from_thread(self.app.refresh_status)
        self.app.call_from_thread(self.app.pop_screen)


# ── Pick Version Screen ─────────────────────────────────────────────────────


class PickVersionScreen(Screen):
    """Select and restore a saved version."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._commits: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(
            Text("Select a saved version to restore", style="bold"),
            id="screen-title")
        yield Static("  Loading versions...", id="loading")
        yield Footer()

    def on_mount(self) -> None:
        self._load_versions()

    @work(thread=True)
    def _load_versions(self) -> None:
        all_commits = git_list_commits(
            self.app.cfg.repo_dir, limit=20)

        commits_data: list[dict] = []
        for commit_line in all_commits:
            parts = commit_line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            commit_hash, timestamp_str, subject = parts
            if "Initial setup" in subject:
                continue
            try:
                dt = datetime.fromisoformat(
                    timestamp_str.replace("Z", "+00:00"))
                formatted_time = dt.strftime(
                    "%B %d, %Y at %I:%M %p")
            except Exception:
                formatted_time = timestamp_str
            commits_data.append({
                "hash": commit_hash,
                "time": formatted_time,
                "subject": subject,
            })

        self.app.call_from_thread(
            self._display_versions, commits_data)

    def _display_versions(self, commits: list[dict]) -> None:
        self._commits = commits

        try:
            self.query_one("#loading").remove()
        except Exception:
            pass

        if not commits:
            self.notify(
                "No saved versions found", severity="information")
            self.app.pop_screen()
            return

        options = [
            Option(c["time"], id=str(i))
            for i, c in enumerate(commits)
        ]

        footer = self.query_one(Footer)
        self.mount(
            OptionList(*options, id="version-list"),
            before=footer)
        self.query_one("#version-list").focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        idx = int(event.option_id)
        self._restore_version(self._commits[idx])

    @work(thread=True, exclusive=True)
    def _restore_version(self, commit: dict) -> None:
        cfg = self.app.cfg
        git_checkout_commit(cfg.repo_dir, commit["hash"])
        imported = import_from_repo_to_slicers(cfg)

        # Return to main branch
        try:
            git_checkout_branch(cfg.repo_dir, "main")
        except subprocess.CalledProcessError:
            try:
                git_checkout_branch(cfg.repo_dir, "master")
            except subprocess.CalledProcessError:
                pass

        n = len(imported)
        if n > 0:
            self.app.call_from_thread(
                self.notify,
                f"✓ Restored {n} file(s) from {commit['time']}",
                severity="information")
        else:
            self.app.call_from_thread(
                self.notify,
                f"Version from {commit['time']}"
                " matches current files",
                severity="information")
        self.app.call_from_thread(self.app.pop_screen)

    def action_go_back(self) -> None:
        self.app.pop_screen()
