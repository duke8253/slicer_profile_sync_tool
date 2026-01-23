#!/usr/bin/env python3
"""
profilesync - Cross-platform slicer profile sync tool

Sync 3D printer slicer profiles (Bambu Studio, OrcaSlicer, etc.) via GitHub.
Supports macOS, Windows, and Linux.
"""

import argparse
import sys
import textwrap

from profilesync.commands import cmd_config, cmd_init, cmd_sync


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="profilesync",
        description="Sync 3D slicer profiles (Bambu Studio, OrcaSlicer) via GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              profilesync init                          # Interactive setup
              profilesync init --remote git@github.com:user/repo.git
              profilesync sync                          # Interactive sync
              profilesync sync --action push            # Push only
              profilesync sync --action pull            # Pull only
              profilesync config                        # Show current config
        """)
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize configuration and clone remote repo"
    )
    init_parser.add_argument(
        "--remote",
        help="GitHub remote URL (SSH or HTTPS)"
    )
    init_parser.add_argument(
        "--repo-dir",
        help="Local clone directory (default: auto-detect from remote)"
    )
    init_parser.add_argument(
        "--editor",
        help="Editor command for conflict resolution (default: 'code --wait')"
    )

    # sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync profiles between local slicers and GitHub"
    )
    sync_parser.add_argument(
        "--action",
        choices=["push", "pull", "pick", "both", "1", "2", "3", "4"],
        help="Sync action: push, pull, pick (version), or both"
    )

    # config command
    config_parser = subparsers.add_parser(
        "config",
        help="Show current configuration"
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "init":
            return cmd_init(args)
        elif args.command == "sync":
            return cmd_sync(args)
        elif args.command == "config":
            return cmd_config(args)
        else:
            parser.print_help()
            return 2
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"\nRun 'profilesync init' first to set up configuration.")
        return 1
    except KeyboardInterrupt:
        from profilesync.ui import info
        print(info("\nAborted. No changes were made to remote or local files."))
        return 130
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
