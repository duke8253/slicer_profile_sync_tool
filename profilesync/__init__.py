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
