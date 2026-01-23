# Slicer Profile Sync Tool

A Python-based tool to sync 3D printer slicer profiles (Bambu Studio and OrcaSlicer) using a private GitHub repository as the sync backend.

## Features

- **Interactive setup** with auto-detection of slicer profile directories on macOS
- **GitHub integration** with SSH/HTTPS support and access validation
- **User-friendly language** - no git jargon, clear explanations
- **Interactive conflict resolution** - guided editor-based conflict fixing
- **Smart sync status** - only shows "Last sync" when profiles actually exist on GitHub
- **Hash-based deduplication** - avoids unnecessary file copies
- **Beautiful formatting** - human-readable timestamps and organized file displays

## Requirements

- Python 3.7+
- Git CLI installed (Xcode Command Line Tools on macOS)
- Private GitHub repository for storing profiles
- SSH keys configured for GitHub (recommended) or HTTPS credentials

## Installation

```bash
# Clone this repository
git clone <your-dev-repo>
cd slicer_profile_sync_tool

# Make the script executable
chmod +x profilesync.py
```

## Usage

### Initial Setup

```bash
./profilesync.py init
```

This will:
1. Validate your GitHub repository access
2. Auto-detect OrcaSlicer and Bambu Studio profile directories
3. Let you select which slicers to sync
4. Configure your preferred editor for conflict resolution
5. Clone your GitHub repository locally

### Syncing Profiles

```bash
./profilesync.py sync
```

Interactive options:
1. **Push** - Save your local profiles to GitHub
2. **Pull** - Download latest profiles from GitHub to your slicer
3. **Pick version** - Restore a specific saved version
4. **Both** - Push then pull (recommended)

### View Configuration

```bash
./profilesync.py config
```

## How It Works

1. **Local Storage**: Profiles are cloned to `./data/<repo-name>/`
2. **Structure**: Files are organized as `profiles/<slicer>/*.json`
3. **Sync Logic**:
   - Export: Copy `.json` files from slicer → local repo
   - Push: Commit and push changes to GitHub
   - Pull: Download from GitHub and rebase local changes
   - Import: Copy `.json` files from local repo → slicer

## Configuration

Config is stored in `./config.json` (gitignored):

```json
{
  "github_remote": "git@github.com:user/repo.git",
  "repo_dir": "./data/repo-name",
  "enabled_slicers": ["orcaslicer", "bambustudio"],
  "slicer_profile_dirs": {
    "orcaslicer": ["/Users/you/Library/Application Support/OrcaSlicer/user/12345"],
    "bambustudio": ["/Users/you/Library/Application Support/BambuStudio/user/12345"]
  },
  "editor_cmd": "code --wait"
}
```

## Future Enhancements

- Windows support (path detection)
- GUI/web interface
- Multi-profile support (home/work environments)
- Subdirectory filtering (filament-only sync)
- Integration with OrcaSlicer
- Packaging as standalone command

## License

MIT
