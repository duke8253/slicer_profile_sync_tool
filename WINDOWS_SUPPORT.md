# Windows Support

## Summary

The profilesync tool now supports Windows in addition to macOS!

## Changes Made

### 1. Platform Detection
- Added `_get_default_slicers()` function that detects the current platform
- Routes to platform-specific slicer detection functions

### 2. Windows Slicer Detection
- Created `_windows_default_slicers()` function
- Detects slicer profiles in `%APPDATA%` directory
- Supports all 5 slicers:
  - Orca Slicer
  - Bambu Studio
  - Snapmaker Orca
  - Creality Print (versions 7.0 and 6.0)
  - Elegoo Slicer

### 3. Path Handling
- Using `pathlib.Path` which handles path separators automatically across platforms
- Windows paths: `C:\Users\USERNAME\AppData\Roaming\`
- macOS paths: `~/Library/Application Support/`

### 4. Documentation Updates
- Updated README.md with Windows installation instructions
- Added platform-specific usage examples
- Documented slicer locations for both macOS and Windows
- Updated feature list to highlight cross-platform support

## Windows Path Locations

All slicers store profiles in `%APPDATA%\<SlicerName>\`:

- **Orca Slicer**: `%APPDATA%\OrcaSlicer\user\<user_id>\`
- **Bambu Studio**: `%APPDATA%\BambuStudio\user\<user_id>\`
- **Snapmaker Orca**: `%APPDATA%\SnapmakerOrcaSlicer\user\<user_id>\`
- **Creality Print**: `%APPDATA%\Creality\Creality Print\7.0\`
- **Elegoo Slicer**: `%APPDATA%\ElegooSlicer\user\<user_id>\`

## Requirements on Windows

1. **Python 3.7+** - Install from [python.org](https://www.python.org/downloads/)
2. **Git for Windows** - Install from [git-scm.com](https://git-scm.com/download/win)
3. **colorama package** - For colored terminal output (install with `pip install -r requirements.txt`)
4. **GitHub SSH keys** (recommended) or HTTPS credentials

## Installation on Windows

```powershell
# Clone the repository
git clone <your-dev-repo>
cd slicer_profile_sync_tool

# Install dependencies (required for colored output on Windows)
pip install -r requirements.txt
```

## Running on Windows

```powershell
# Initial setup
python profilesync.py init

# Sync profiles
python profilesync.py sync

# View configuration
python profilesync.py config
```

## Color Support on Windows

ANSI color codes work on:
- Windows 10 version 1511+ (default)
- Windows Terminal
- PowerShell 5.1+
- Command Prompt (with VT100 enabled)

Colors automatically disable when output is redirected to files.

## Known Limitations

- Git must be installed and available in PATH
- SSH keys setup for GitHub is recommended (HTTPS works but requires credentials)
- Windows Defender may scan files during sync (minor performance impact)

## Testing

To test on Windows:
1. Install Git for Windows
2. Install Python 3.7+
3. Clone your private GitHub repository for profiles
4. Run `python profilesync.py init`
5. Follow the interactive setup

## Future Enhancements

- Linux support (coming soon)
- Standalone executable packaging for Windows (.exe)
- Windows installer
- Registry integration for context menu actions
