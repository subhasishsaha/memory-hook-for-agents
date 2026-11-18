"""Command-line interface for memory-hook installation and inspection."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .constants import (
        BIN_DIR_NAME,
        LONG_LOG_NAME,
        MEMORY_DIR_NAME,
        RUNNER_COMPILED_NAME,
        SHORT_LOG_NAME,
        STATE_FILE_NAME,
        SUPPORTED_PROVIDERS,
    )
    from .installer import install_hooks, verify_runner_integrity
    from .runner import main as runner_main, run_hook
    from .state import MemoryState
except (ImportError, ValueError):
    pkg_dir = Path(__file__).resolve().parent.parent
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    from memory_hook.constants import (
        BIN_DIR_NAME,
        LONG_LOG_NAME,
        MEMORY_DIR_NAME,
        RUNNER_COMPILED_NAME,
        SHORT_LOG_NAME,
        STATE_FILE_NAME,
        SUPPORTED_PROVIDERS,
    )
    from memory_hook.installer import install_hooks, verify_runner_integrity
    from memory_hook.runner import main as runner_main, run_hook
    from memory_hook.state import MemoryState


def cmd_install(args: argparse.Namespace) -> None:
    """Handles the install command."""
    target = args.path or getattr(args, "target_path", None)
    target_path = Path(target).resolve() if target else Path.cwd().resolve()
    providers = [args.provider] if args.provider else ["all"]
    dry_run = getattr(args, "dry_run", False)

    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"{prefix}Installing cross-provider memory-hook in: {target_path}")
    installed = install_hooks(target_path, providers=providers, dry_run=dry_run)
    print(f"\n{prefix}[SUCCESS] Memory hook {'planned' if dry_run else 'successfully installed'}!\n")
    for prov, files in installed.items():
        print(f"  • {prov.upper()}:")
        for f in files:
            print(f"      - {f}")
    if not dry_run:
        print("\nInitialized files:")
        print(f"  • .memory/{SHORT_LOG_NAME} (Deterministic event log)")
        print(f"  • .memory/{LONG_LOG_NAME} (High-density distilled memory)")
        print(f"  • .memory/{STATE_FILE_NAME} (Event sequence and compaction state)")
        print(f"  • .memory/bin/{RUNNER_COMPILED_NAME} (Compiled, locked runner)")


def cmd_status(args: argparse.Namespace) -> None:
    """Handles the status command with comprehensive diagnostics and token estimates."""
    target = args.path or getattr(args, "target_path", None)
    target_path = Path(target).resolve() if target else Path.cwd().resolve()
    memory_dir = target_path / MEMORY_DIR_NAME
    if not memory_dir.is_dir():
        print(f"No .memory directory found in {target_path}. Run 'memory-hook install' first.")
        sys.exit(1)

    state = MemoryState(memory_dir)
    short_log = memory_dir / SHORT_LOG_NAME
    long_log = memory_dir / LONG_LOG_NAME

    line_count = 0
    short_size = 0
    if short_log.is_file():
        try:
            short_size = short_log.stat().st_size
            with open(short_log, "r", encoding="utf-8", errors="ignore") as f:
                line_count = sum(1 for line in f if line.strip())
        except Exception:
            pass

    long_size = long_log.stat().st_size if long_log.is_file() else 0
    long_mtime = "Never"
    if long_log.is_file():
        try:
            mtime_dt = datetime.fromtimestamp(long_log.stat().st_mtime, tz=timezone.utc)
            long_mtime = mtime_dt.isoformat()
        except Exception:
            pass

    # Check installed providers
    installed_providers = []
    if (target_path / ".agents" / "hooks.json").is_file():
        installed_providers.append("antigravity")
    if (target_path / ".claude" / "settings.json").is_file():
        installed_providers.append("claude-code")
    if (target_path / ".codex" / "hooks.json").is_file():
        installed_providers.append("codex")
    if (target_path / ".cursor" / "hooks.json").is_file():
        installed_providers.append("cursor")

    # Verify runner binary integrity
    ok_int, int_msg = verify_runner_integrity(memory_dir)

    print(f"=== Memory Hook Status: {target_path.name} ===")
    print(f"Memory Directory:        {memory_dir}")
    print(f"Installed Providers:     {', '.join(installed_providers) if installed_providers else 'None detected'}")
    print(f"Runner Integrity:        {int_msg}")
    print(f"Current Sequence:        #{state.current_seq}")
    print(f"Window Start Sequence:   #{state.window_start_seq}")
    print(f"Last Compacted Sequence: #{state.last_compacted_seq}")
    print(f"Total Logged Events:     {line_count}")
    print(f"short.log Size:          {short_size:,} bytes")
    print(f"long.log Size:           {long_size:,} bytes (Last Modified: {long_mtime})")
    print(f"Uncompacted Tokens:      ~{state.uncompacted_tokens:,} / 10,000 (estimated) (Compaction Due: {'Yes' if state.is_compaction_due() else 'No'})")
    print(f"Rolling Context Tokens:  ~{state.short_log_tokens:,} / 100,000 (estimated)")
    print(f"Lifetime Tracked Tokens: ~{state.total_lifetime_tokens:,} (estimated)")

    pc = state.pending_compaction
    if pc and isinstance(pc, dict) and pc.get("status") == "pending":
        print(f"Pending Compaction:      PENDING since event #{pc.get('requested_at_seq')} (~{pc.get('requested_tokens'):,} tokens, requested at {pc.get('requested_at')})")
    else:
        print(f"Pending Compaction:      None (all events up to #{state.last_compacted_seq} acknowledged)")

    print(f"Last Updated:            {state.data.get('last_updated_ts') or 'Never'}")
    print(f"Last Active Provider:    {state.data.get('last_updated_provider') or 'None'}")


def cmd_acknowledge(args: argparse.Namespace) -> None:
    """Explicitly acknowledges compaction through a specified sequence."""
    target = args.path or getattr(args, "target_path", None)
    target_path = Path(target).resolve() if target else Path.cwd().resolve()
    memory_dir = target_path / MEMORY_DIR_NAME
    if not memory_dir.is_dir():
        print(f"No .memory directory found in {target_path}.")
        sys.exit(1)

    state = MemoryState(memory_dir)
    target_seq = args.seq or state.current_seq
    force = getattr(args, "force", False)
    try:
        changed = state.acknowledge_compaction(target_seq, force=force)
        if changed:
            print(f"[SUCCESS] Compaction acknowledged through event #{state.last_compacted_seq}.")
        else:
            print(f"[NO-OP] State already acknowledged up to or beyond event #{target_seq}.")
    except ValueError as e:
        print(f"[ERROR] Invalid acknowledgement: {e}")
        sys.exit(1)


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspects recent entries in short.log or long.log."""
    target = args.path or getattr(args, "target_path", None)
    target_path = Path(target).resolve() if target else Path.cwd().resolve()
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
    with open(short_log, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    tail_count = args.tail or 10
    recent = lines[-tail_count:]
    show_payload = getattr(args, "show_payload", False)
    print(f"--- Showing last {len(recent)} events from {SHORT_LOG_NAME} ---")
    for line in recent:
        try:
            parsed = json.loads(line)
            seq = parsed.get("seq")
            ts = parsed.get("ts")
            prov = parsed.get("provider")
            ev_type = parsed.get("type")
            tokens = parsed.get("tokens", 0)
            if show_payload:
                print(f"[{seq}] [{ts}] [{prov}] {ev_type} ({tokens} tokens): {parsed.get('payload')}")
            else:
                payload = parsed.get("payload") or {}
                summary = ""
                if isinstance(payload, dict):
                    if "tool_name" in payload:
                        summary = f"tool={payload.get('tool_name')}"
                    elif "content" in payload:
                        c = str(payload.get("content", ""))
                        summary = f"content_len={len(c)}"
                    else:
                        summary = f"keys={list(payload.keys())}"
                else:
                    summary = f"len={len(str(payload))}"
                print(f"[{seq}] [{ts}] [{prov}] {ev_type} ({tokens} tokens) [payload masked, pass --show-payload to view]: {summary}")
        except Exception:
            print(line.strip())


def cmd_run(args: argparse.Namespace) -> None:
    """Handles the run command by delegating directly to run_hook."""
    raw_input_data = ""
    try:
        if not sys.stdin.isatty():
            raw_input_data = sys.stdin.read()
    except Exception:
        raw_input_data = ""

    try:
        root_path = Path(args.root).resolve() if args.root else None
        result = run_hook(
            event_hint=args.event,
            provider_hint=args.provider,
            raw_input_data=raw_input_data,
            project_root=root_path,
        )
        sys.stdout.write(json.dumps(result))
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"memory-hook runner warning: {exc}\n")
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"status": "ok", "decision": "allow"}))
        sys.stdout.flush()


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="memory-hook",
        description="Downloadable cross-provider memory hook for Claude, Antigravity, Codex, Cursor.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # install
    install_p = subparsers.add_parser("install", help="Install memory-hook into a project")
    install_p.add_argument("target_path", nargs="?", default=None, help="Target project root directory")
    install_p.add_argument("--path", type=str, help="Target project root directory (defaults to current dir)")
    install_p.add_argument(
        "--provider",
        type=str,
        choices=["all", "antigravity", "claude", "codex", "cursor"],
        default="all",
        help="Provider to configure (default: all)",
    )
    install_p.add_argument("--dry-run", action="store_true", help="Show proposed changes without writing")

    # status
    status_p = subparsers.add_parser("status", help="Show memory-hook health and counters")
    status_p.add_argument("target_path", nargs="?", default=None, help="Target project directory")
    status_p.add_argument("--path", type=str, help="Target project directory")

    # ack / acknowledge
    for ack_cmd in ["ack", "acknowledge"]:
        ack_p = subparsers.add_parser(ack_cmd, help="Acknowledge long-term compaction through sequence")
        ack_p.add_argument("target_path", nargs="?", default=None, help="Target project directory")
        ack_p.add_argument("--path", type=str, help="Target project directory")
        ack_p.add_argument("--seq", type=int, default=None, help="Sequence number to acknowledge through")
        ack_p.add_argument("--force", action="store_true", help="Force acknowledgement beyond pending compaction checkpoint")

    # inspect
    inspect_p = subparsers.add_parser("inspect", help="Inspect short.log or long.log entries")
    inspect_p.add_argument("target_path", nargs="?", default=None, help="Target project directory")
    inspect_p.add_argument("--path", type=str, help="Target project directory")
    inspect_p.add_argument("--tail", type=int, default=10, help="Number of recent short.log events to show")
    inspect_p.add_argument("--long", action="store_true", help="Print long.log content instead")
    inspect_p.add_argument("--show-payload", action="store_true", help="Print full payload bodies (masked by default)")

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
    elif args.subcommand in ["ack", "acknowledge"]:
        cmd_acknowledge(args)
    elif args.subcommand == "inspect":
        cmd_inspect(args)
    elif args.subcommand == "run":
        cmd_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
