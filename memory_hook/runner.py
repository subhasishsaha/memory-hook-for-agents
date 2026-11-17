"""Universal lifecycle hook runner executed by host agents."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import (
    EVENT_MODEL_RESPONSE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_REQUEST,
    LONG_LOG_NAME,
    MEMORY_DIR_NAME,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
    SHORT_LOG_NAME,
)
from .normalizer import detect_provider, normalize_event
from .state import MemoryState


def find_project_root(start_dir: Optional[Path] = None) -> Path:
    """Finds project root by locating .memory, .git, or standard repo markers."""
    current = Path(start_dir or os.getcwd()).resolve()
    for parent in [current] + list(current.parents):
        if (parent / MEMORY_DIR_NAME).is_dir() or (parent / ".git").exists():
            return parent
    return current


def append_to_short_log(memory_dir: Path, event: Dict[str, Any]) -> None:
    """Safely appends a JSONL record to short.log."""
    short_log_path = memory_dir / SHORT_LOG_NAME
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(short_log_path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass


def get_compaction_notice(seq: int, uncompacted_count: int) -> str:
    """Returns the standardized prompt instruction for updating long.log."""
    return (
        f"[MEMORY HOOK UPDATE NOTICE]: {uncompacted_count} actions have elapsed since the "
        f"last long-term memory update (Current Event: #{seq}). "
        f"Action Required: Review the uncompacted entries in `.memory/short.log` and your current conversation progress. "
        f"Use your file editing tools to update `.memory/long.log` to preserve the latest project status, "
        f"architectural decisions, completed milestones, and next steps."
    )


def run_hook(
    event_hint: Optional[str] = None,
    provider_hint: Optional[str] = None,
    raw_input_data: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Core logic to process an incoming hook event."""
    root = find_project_root(project_root)
    memory_dir = root / MEMORY_DIR_NAME
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Parse raw stdin input
    raw_payload: Dict[str, Any] = {}
    if raw_input_data:
        try:
            raw_payload = json.loads(raw_input_data)
        except Exception:
            raw_payload = {"raw_text": raw_input_data}

    # Normalize the event
    provider = detect_provider(raw_payload, provider_hint)
    event = normalize_event(raw_payload, event_hint=event_hint, provider_hint=provider)

    # Sequence numbering and state tracking
    state = MemoryState(memory_dir)
    seq = state.next_seq(provider=provider)
    event["seq"] = seq

    # Write to short.log
    append_to_short_log(memory_dir, event)

    # Check compaction condition
    compaction_due = state.is_compaction_due()
    uncompacted_count = seq - state.last_compacted_seq

    # Construct host provider response
    response: Dict[str, Any] = {}

    if provider == PROVIDER_ANTIGRAVITY:
        event_name = event_hint or raw_payload.get("hookEventName") or ""
        if event_name == "PreToolUse":
            response = {"decision": "allow"}
        elif event_name == "PostToolUse":
            response = {}
        elif event_name in ["PreInvocation", "SessionStart"]:
            if compaction_due:
                notice = get_compaction_notice(seq, uncompacted_count)
                response = {
                    "injectSteps": [
                        {
                            "systemMessage": {
                                "systemMessage": notice
                            }
                        }
                    ]
                }
                # Once notice is injected, mark state so we don't spam every tool call in turn
                state.mark_compacted(seq)
            else:
                response = {}
        elif event_name == "Stop":
            response = {"decision": "allow"}
        else:
            response = {}

    elif provider == PROVIDER_CLAUDE:
        if compaction_due:
            notice = get_compaction_notice(seq, uncompacted_count)
            # Writing to stderr in Claude Code passes feedback back to model context
            sys.stderr.write(f"\n{notice}\n")
            sys.stderr.flush()
            state.mark_compacted(seq)
        response = {"status": "ok"}

    else:
        if compaction_due:
            notice = get_compaction_notice(seq, uncompacted_count)
            sys.stderr.write(f"\n{notice}\n")
            sys.stderr.flush()
            state.mark_compacted(seq)
        response = {"status": "ok"}

    return response


def main() -> None:
    """CLI entrypoint for executing the hook."""
    parser = argparse.ArgumentParser(description="Cross-provider memory hook runner")
    parser.add_argument("--event", type=str, help="Lifecycle event name")
    parser.add_argument("--provider", type=str, help="Provider identifier hint")
    parser.add_argument("--root", type=str, help="Project root directory path")
    args = parser.parse_args()

    # Read stdin safely
    raw_input_data = ""
    try:
        if not sys.stdin.isatty():
            raw_input_data = sys.stdin.read()
    except Exception:
        pass

    try:
        root_path = Path(args.root) if args.root else None
        result = run_hook(
            event_hint=args.event,
            provider_hint=args.provider,
            raw_input_data=raw_input_data,
            project_root=root_path,
        )
        # Output clean JSON on stdout for host agent
        sys.stdout.write(json.dumps(result))
        sys.stdout.flush()
        sys.exit(0)
    except Exception as exc:
        # Never crash host agent loop - log to stderr and exit 0
        sys.stderr.write(f"memory-hook runner warning: {exc}\n")
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"status": "ok", "decision": "allow"}))
        sys.stdout.flush()
        sys.exit(0)


if __name__ == "__main__":
    main()
