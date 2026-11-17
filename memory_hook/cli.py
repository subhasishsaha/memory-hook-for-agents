"""Command-line interface for memory-hook installation and inspection."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .constants import (
    LONG_LOG_NAME,
    MEMORY_DIR_NAME,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
    SUPPORTED_PROVIDERS,
)
from .installer import install_hooks
from .runner import main as runner_main
from .state import MemoryState


def cmd_install(args: argparse.Namespace) -> None:
    """Handles the install command."""
    target_path = Path(args.path).resolve() if args.path else Path.cwd().resolve()
    providers = [args.provider] if args.provider else ["all"]
    print(f"Installing cross-provider memory-hook in: {target_path}")
    installed = install_hooks(target_path, providers=providers)
    print("\n[SUCCESS] Memory hook successfully installed!\n")
    for prov, files in installed.items():
        print(f"  • {prov.upper()}:")
        for f in files:
            print(f"      - {f}")
    print("\nInitialized files:")
    print(f"  • .memory/{SHORT_LOG_NAME} (Deterministic event log)")
    print(f"  • .memory/{LONG_LOG_NAME} (High-density distilled memory)")
    print(f"  • .memory/{STATE_FILE_NAME} (Event sequence and compaction state)")
    print(f"  • .memory/bin/memory_hook_runner.py (Standalone portable runner)")


def cmd_status(args: argparse.Namespace) -> None:
    """Handles the status command."""
    target_path = Path(args.path).resolve() if args.path else Path.cwd().resolve()
    memory_dir = target_path / MEMORY_DIR_NAME
    if not memory_dir.is_dir():
        print(f"No .memory directory found in {target_path}. Run 'memory-hook install' first.")
        sys.exit(1)

    state = MemoryState(memory_dir)
    short_log = memory_dir / SHORT_LOG_NAME
    long_log = memory_dir / LONG_LOG_NAME

    line_count = 0
    if short_log.is_file():
        with open(short_log, "r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

    long_size = long_log.stat().st_size if long_log.is_file() else 0

    print(f"=== Memory Hook Status for: {target_path.name} ===")
    print(f"Current Sequence:        #{state.current_seq}")
    print(f"Last Compacted Sequence: #{state.last_compacted_seq}")
    print(f"Uncompacted Actions:     {state.current_seq - state.last_compacted_seq} (Threshold: 10)")
    print(f"Total Logged Events:     {line_count}")
    print(f"Last Updated:            {state.data.get('last_updated_ts') or 'Never'}")
    print(f"Last Active Provider:    {state.data.get('last_updated_provider') or 'None'}")
    print(f"long.log Size:           {long_size} bytes")


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspects recent entries in short.log or long.log."""
    target_path = Path(args.path).resolve() if args.path else Path.cwd().resolve()
    memory_dir = target_path / MEMORY_DIR_NAME
    short_log = memory_dir / SHORT_LOG_NAME
    long_log = memory_dir / LONG_LOG_NAME

    if args.long:
        if not long_log.is_file():
            print("No long.log found.")
            return
        with open(long_log, "r", encoding="utf-8") as f:
            print(f.read())
        return

    if not short_log.is_file():
        print("No short.log found.")
        return

    lines = []
    with open(short_log, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tail_count = args.tail or 10
    recent = lines[-tail_count:]
    print(f"--- Showing last {len(recent)} events from {SHORT_LOG_NAME} ---")
    for line in recent:
        try:
            parsed = json.loads(line)
            print(f"[{parsed.get('seq')}] [{parsed.get('ts')}] [{parsed.get('provider')}] {parsed.get('type')}: {parsed.get('payload')}")
        except Exception:
            print(line.strip())


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="memory-hook",
        description="Downloadable cross-provider memory hook for Claude, Antigravity, Codex, Cursor.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # install
    install_p = subparsers.add_parser("install", help="Install memory-hook into a project")
    install_p.add_argument("--path", type=str, help="Target project root directory (defaults to current dir)")
    install_p.add_argument(
        "--provider",
        type=str,
        choices=["all", "antigravity", "claude", "codex", "cursor"],
        default="all",
        help="Provider to configure (default: all)",
    )

    # status
    status_p = subparsers.add_parser("status", help="Show memory-hook health and counters")
    status_p.add_argument("--path", type=str, help="Target project directory")

    # inspect
    inspect_p = subparsers.add_parser("inspect", help="Inspect short.log or long.log entries")
    inspect_p.add_argument("--path", type=str, help="Target project directory")
    inspect_p.add_argument("--tail", type=int, default=10, help="Number of recent short.log events to show")
    inspect_p.add_argument("--long", action="store_true", help="Print long.log content instead")

    # runner passthrough
    run_p = subparsers.add_parser("run", help="Internal runner invoked by agent hooks")
    run_p.add_argument("--event", type=str, help="Lifecycle event name")
    run_p.add_argument("--provider", type=str, help="Provider name")
    run_p.add_argument("--root", type=str, help="Project root directory")

    args = parser.parse_args()

    if args.subcommand == "install":
        cmd_install(args)
    elif args.subcommand == "status":
        cmd_status(args)
    elif args.subcommand == "inspect":
        cmd_inspect(args)
    elif args.subcommand == "run":
        runner_main()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
