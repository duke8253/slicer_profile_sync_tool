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

"""UI utilities for profilesync - colors, prompts, and display functions."""

from __future__ import annotations

import platform
import sys

# Cross-platform colored terminal output
COLORAMA_AVAILABLE = False
try:
    import colorama
    colorama.init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    # colorama not installed - colors will be disabled
    pass


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


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask user for yes/no confirmation"""
    suffix = " [Y/n] " if default else " [y/N] "
    ans = input(prompt + suffix).strip().lower()
    if ans == "":
        return default
    return ans in ("y", "yes")
