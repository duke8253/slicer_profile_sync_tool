"""profilesync - Sync 3D slicer profiles via GitHub."""

__version__ = "2.0.0"

from .commands import cmd_config, cmd_init, cmd_sync
from .config import Config, DEFAULT_CONFIG_DIR, DEFAULT_DATA_DIR
from .git import ensure_git_available
from .slicers import get_default_slicers, Slicer
from .sync import export_from_slicers_to_repo, import_from_repo_to_slicers
from .ui import confirm, error, info, success, warning

__all__ = [
    "__version__",
    "cmd_config",
    "cmd_init",
    "cmd_sync",
    "Config",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_DATA_DIR",
    "ensure_git_available",
    "get_default_slicers",
    "Slicer",
    "export_from_slicers_to_repo",
    "import_from_repo_to_slicers",
    "confirm",
    "error",
    "info",
    "success",
    "warning",
]
