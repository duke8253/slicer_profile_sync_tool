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

import difflib
import subprocess
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
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
    export_from_slicers_to_repo,
    export_selected_to_repo,
    group_by_slicer_and_type,
    import_from_repo_to_slicers,
    import_selected_profiles,
    rebuild_exported_from_git,
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

    # Changes detected (local slicer files vs what's on the server)
    if exported:
        total = len(exported)
        text.append(
            f"  ● {total} file(s) differ from server\n",
            style="yellow",
        )
        # Show breakdown by slicer and type
        grouped = group_by_slicer_and_type(
            exported, cfg, cfg.repo_dir, use_dst_for_type=True)
        for slicer_key, types in grouped.items():
            display = SLICER_DISPLAY_NAMES.get(
                slicer_key, slicer_key.capitalize())
            type_parts = [
                f"{len(files)} {ptype}"
                for ptype, files in sorted(types.items())
            ]
            text.append(f"    {display}: ", style="bold")
            text.append(", ".join(type_parts) + "\n")
    else:
        text.append(
            "  ✓ Local profiles match server\n",
            style="green",
        )

    # Server sync status
    if remote_has_commits:
        local_head = run(
            ["git", "rev-parse", "HEAD"],
            cwd=cfg.repo_dir, check=False,
        ).stdout.strip()
        remote_head = run(
            ["git", "rev-parse", "origin/main"],
            cwd=cfg.repo_dir, check=False,
        ).stdout.strip()

        if local_head == remote_head and not exported:
            text.append(
                "  ✓ Everything is synced\n", style="green")
        elif local_head != remote_head:
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
                        "  ⚠ Local and server have diverged\n",
                        style="yellow")
                elif behind > 0:
                    text.append(
                        f"  ↓ Server has {behind} newer update(s)\n",
                        style="cyan")
    else:
        if not exported:
            text.append(
                "  ℹ No profiles saved to server yet\n", style="dim")

    return text


# ── Base Screen ──────────────────────────────────────────────────────────────


class SyncScreen(Screen):
    """Base screen with a typed reference to the SyncApp."""

    @property
    def sync_app(self) -> "SyncApp":
        return self.app  # type: ignore[return-value]


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

    #diff-title {
        padding: 1 2;
    }

    #diff-container {
        height: 1fr;
        margin: 0 1;
    }

    #diff-content {
        height: auto;
    }

    .diff-pane {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    .diff-header-row {
        height: auto;
        margin: 0 1;
    }

    .diff-header {
        text-style: bold;
        width: 1fr;
        padding: 0 2;
        color: $text;
        background: $surface;
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


# ── Diff Screen ─────────────────────────────────────────────────────────────


class DiffScreen(SyncScreen):
    """Display a side-by-side diff between two file versions."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back", show=False),
    ]

    def __init__(
        self,
        filename: str,
        left_text: str,
        right_text: str,
        left_label: str = "local",
        right_label: str = "server",
    ) -> None:
        super().__init__()
        self._filename = filename
        self._left_text = left_text
        self._right_text = right_text
        self._left_label = left_label
        self._right_label = right_label

    def compose(self) -> ComposeResult:
        left_content, right_content, changed_summary = (
            self._build_side_by_side())

        title = Text()
        title.append(f"  Diff: {self._filename}", style="bold")
        title.append(
            f"  ({self._left_label} → {self._right_label})", style="dim")
        if changed_summary:
            title.append(f"  {changed_summary}", style="yellow")
        yield Static(title, id="diff-title")

        with Horizontal(classes="diff-header-row"):
            yield Static(self._left_label, classes="diff-header")
            yield Static(self._right_label, classes="diff-header")

        with ScrollableContainer(id="diff-container"):
            with Horizontal(id="diff-content"):
                yield Static(left_content, classes="diff-pane")
                yield Static(right_content, classes="diff-pane")
        yield Footer()

    def _build_side_by_side(self) -> tuple[Text, Text, str]:
        """Build aligned left/right Rich Text panels with line numbers.

        Returns (left_text, right_text, changed_lines_summary).
        """
        left_lines = self._left_text.splitlines()
        right_lines = self._right_text.splitlines()

        if left_lines == right_lines:
            msg = Text("  Files are identical", style="dim italic")
            return msg, msg.copy(), ""

        left = Text()
        right = Text()
        changed_line_nums: list[int] = []
        row = 0  # visual row counter for the summary
        left_num = 0
        right_num = 0

        sm = difflib.SequenceMatcher(None, left_lines, right_lines)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    left_num += 1
                    right_num += 1
                    row += 1
                    left.append(f"{left_num:4d} ", style="dim")
                    left.append(f"{left_lines[i1 + k]}\n")
                    right.append(f"{right_num:4d} ", style="dim")
                    right.append(f"{right_lines[j1 + k]}\n")
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    row += 1
                    if i1 + k < i2:
                        left_num += 1
                        changed_line_nums.append(left_num)
                        left.append(f"{left_num:4d} ", style="red")
                        left.append(
                            f"{left_lines[i1 + k]}\n", style="red")
                    else:
                        left.append("     \n", style="dim")
                    if j1 + k < j2:
                        right_num += 1
                        right.append(f"{right_num:4d} ", style="green")
                        right.append(
                            f"{right_lines[j1 + k]}\n", style="green")
                    else:
                        right.append("     \n", style="dim")
            elif tag == "delete":
                for k in range(i2 - i1):
                    left_num += 1
                    row += 1
                    changed_line_nums.append(left_num)
                    left.append(f"{left_num:4d} ", style="red")
                    left.append(
                        f"{left_lines[i1 + k]}\n", style="red")
                    right.append("     \n", style="dim")
            elif tag == "insert":
                for k in range(j2 - j1):
                    right_num += 1
                    row += 1
                    left.append("     \n", style="dim")
                    right.append(f"{right_num:4d} ", style="green")
                    right.append(
                        f"{right_lines[j1 + k]}\n", style="green")

        summary = self._summarize_changed_lines(changed_line_nums)
        return left, right, summary

    @staticmethod
    def _summarize_changed_lines(nums: list[int]) -> str:
        """Collapse a list of line numbers into a compact range string."""
        if not nums:
            return ""
        ranges: list[str] = []
        start = nums[0]
        end = nums[0]
        for n in nums[1:]:
            if n == end + 1:
                end = n
            else:
                ranges.append(
                    str(start) if start == end else f"{start}-{end}")
                start = end = n
        ranges.append(str(start) if start == end else f"{start}-{end}")
        return f"[lines {', '.join(ranges)}]"

    def action_go_back(self) -> None:
        self.sync_app.pop_screen()


# ── Main Screen ─────────────────────────────────────────────────────────────


class MainScreen(SyncScreen):
    """Displays sync status and action menu."""

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
    ]

    def on_screen_resume(self) -> None:
        """Refresh the status panel every time this screen becomes active."""
        self.query_one("#status-panel", Static).update(
            self.sync_app.status_text)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.sync_app.status_text, id="status-panel")
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
            if self.sync_app.exported:
                self.sync_app.push_screen(PushScreen())
            else:
                self.notify(
                    "Nothing to push — local profiles match server",
                    severity="information")
        elif oid == "pull":
            self.sync_app.push_screen(PullScreen())
        elif oid == "full_sync":
            if self.sync_app.exported:
                self.sync_app.push_screen(PushScreen(then_pull=True))
            else:
                # Nothing to push, go directly to pull
                self.sync_app.push_screen(PullScreen())
        elif oid == "pick":
            self.sync_app.push_screen(PickVersionScreen())

    def action_quit_app(self) -> None:
        self.sync_app.exit(0)


# ── Push Screen ─────────────────────────────────────────────────────────────


class PushScreen(SyncScreen):
    """Select files to push, then execute push in a worker."""

    BINDINGS = [
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("i", "invert", "Invert"),
        Binding("s", "range_select", "Range"),
        Binding("d", "show_diff", "Diff"),
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
        for i, (src, dst) in enumerate(self.sync_app.exported):
            selections.append((self._make_label(src, dst), i, True))

        yield SelectionList[int](*selections, id="file-list")
        yield Static("", id="select-status")
        yield Footer()

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_label(self, src: Path | None, dst: Path) -> Text:
        cfg = self.sync_app.cfg
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

    def action_show_diff(self) -> None:
        """Show diff for the highlighted file (exported vs last commit)."""
        sl = self.query_one(SelectionList)
        idx = sl.highlighted
        if idx is None:
            return
        src, dst = self.sync_app.exported[idx]

        if src is None:
            self.notify("File was deleted — no diff to show",
                        severity="information")
            return

        # New content: the local slicer file (what we want to push)
        try:
            new_text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            new_text = ""

        # Old content: what git has at HEAD for this path
        try:
            rel = dst.relative_to(self.sync_app.cfg.repo_dir)
            result = run(
                ["git", "show", f"HEAD:{rel}"],
                cwd=self.sync_app.cfg.repo_dir, check=False,
            )
            old_text = result.stdout if result.returncode == 0 else ""
        except (ValueError, Exception):
            old_text = ""

        self.sync_app.push_screen(DiffScreen(
            filename=dst.name,
            left_text=new_text,
            right_text=old_text,
            left_label="local",
            right_label="server",
        ))

    def action_confirm(self) -> None:
        selected = list(self.query_one(SelectionList).selected)
        if not selected:
            self.notify("No files selected", severity="warning")
            return
        self._execute_push(selected)

    def action_go_back(self) -> None:
        self.sync_app.pop_screen()

    # ── worker ───────────────────────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _execute_push(self, selected_indices: list[int]) -> None:
        cfg = self.sync_app.cfg
        exported = self.sync_app.exported
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
            self.sync_app.call_from_thread(
                self.notify, "✓ Already synced to server",
                severity="information")
            self.sync_app.call_from_thread(self._after_push, True)
            return

        # Pull-rebase before pushing
        try:
            git_pull_rebase(cfg.repo_dir)
        except subprocess.CalledProcessError:
            if git_has_conflicts(cfg.repo_dir):
                run(["git", "rebase", "--abort"],
                    cwd=cfg.repo_dir, check=False)
                self.sync_app.call_from_thread(
                    self.notify,
                    "Conflicts detected — resolve manually and retry",
                    severity="error", timeout=10)
            else:
                self.sync_app.call_from_thread(
                    self.notify, "Error syncing with server",
                    severity="error", timeout=10)
            self.sync_app.call_from_thread(self._after_push, False)
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
            self.sync_app.call_from_thread(
                self.notify, f"✓ Pushed {n} file(s) to server",
                severity="information")
            self.sync_app.call_from_thread(self._after_push, True)
        except subprocess.CalledProcessError:
            self.sync_app.call_from_thread(
                self.notify, "Push failed", severity="error", timeout=10)
            self.sync_app.call_from_thread(self._after_push, False)

    def _after_push(self, success: bool) -> None:
        if success:
            # Re-export to detect any remaining unpushed changes
            exported = export_from_slicers_to_repo(self.sync_app.cfg)
            if not exported:
                exported = rebuild_exported_from_git(self.sync_app.cfg)
            self.sync_app.exported = exported
            self.sync_app.refresh_status()
        self.sync_app.pop_screen()
        if success and self.then_pull:
            self.sync_app.push_screen(PullScreen())


# ── Pull Screen ─────────────────────────────────────────────────────────────


class PullScreen(SyncScreen):
    """Load server profiles, let user select, and import."""

    BINDINGS = [
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("i", "invert", "Invert"),
        Binding("s", "range_select", "Range"),
        Binding("d", "show_diff", "Diff"),
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
        cfg = self.sync_app.cfg

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
            self.sync_app.call_from_thread(
                self.notify,
                "Error downloading from server",
                severity="error", timeout=10)
            self.sync_app.call_from_thread(self.sync_app.pop_screen)
            return

        profiles = collect_server_profiles(cfg)
        self.sync_app.call_from_thread(self._display_profiles, profiles)

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
            self.sync_app.pop_screen()
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

    def action_show_diff(self) -> None:
        """Show diff for the highlighted profile (server vs local slicer)."""
        try:
            sl = self.query_one(SelectionList)
        except Exception:
            return
        idx = sl.highlighted
        if idx is None:
            return
        p = self._profiles[idx]

        # Server version (in repo)
        repo_path: Path = p["repo_path"]
        try:
            new_text = repo_path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            new_text = ""

        # Local slicer version
        local_path: Path | None = p.get("local_path")
        if local_path and local_path.exists():
            try:
                old_text = local_path.read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                old_text = ""
        else:
            old_text = ""

        self.sync_app.push_screen(DiffScreen(
            filename=p["filename"],
            left_text=old_text,
            right_text=new_text,
            left_label="local",
            right_label="server",
        ))

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
        self.sync_app.pop_screen()

    @work(thread=True, exclusive=True)
    def _execute_pull(self, selected_indices: list[int]) -> None:
        selected = [self._profiles[i] for i in selected_indices]
        imported = import_selected_profiles(self.sync_app.cfg, selected)

        n = len(imported)
        if n > 0:
            self.sync_app.call_from_thread(
                self.notify,
                f"✓ Downloaded {n} file(s) to local",
                severity="information")
        else:
            self.sync_app.call_from_thread(
                self.notify,
                "All selected files are already up to date",
                severity="information")

        # Re-export to recalculate remaining differences
        exported = export_from_slicers_to_repo(self.sync_app.cfg)
        if not exported:
            exported = rebuild_exported_from_git(self.sync_app.cfg)
        self.sync_app.exported = exported

        # Refresh main screen status after pull
        self.sync_app.call_from_thread(self.sync_app.refresh_status)
        self.sync_app.call_from_thread(self.sync_app.pop_screen)


# ── Pick Version Screen ─────────────────────────────────────────────────────


class PickVersionScreen(SyncScreen):
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
            self.sync_app.cfg.repo_dir, limit=20)

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

        self.sync_app.call_from_thread(
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
            self.sync_app.pop_screen()
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
        if event.option_id is None:
            return
        idx = int(event.option_id)
        self._restore_version(self._commits[idx])

    @work(thread=True, exclusive=True)
    def _restore_version(self, commit: dict) -> None:
        cfg = self.sync_app.cfg
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
            self.sync_app.call_from_thread(
                self.notify,
                f"✓ Restored {n} file(s) from {commit['time']}",
                severity="information")
        else:
            self.sync_app.call_from_thread(
                self.notify,
                f"Version from {commit['time']}"
                " matches current files",
                severity="information")
        self.sync_app.call_from_thread(self.sync_app.pop_screen)

    def action_go_back(self) -> None:
        self.sync_app.pop_screen()
