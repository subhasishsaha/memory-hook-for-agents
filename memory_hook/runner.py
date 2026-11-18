"""Universal lifecycle hook runner executed by host agents."""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import (
    DEFAULT_COMPACT_TOKEN_INTERVAL,
    EVENT_MODEL_RESPONSE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_REQUEST,
    LONG_LOG_NAME,
    MAX_SHORT_LOG_TOKENS,
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


def migrate_legacy_txt_files(memory_dir: Path) -> None:
    """Migrates any legacy .txt files (short.txt, long.txt) to .log files."""
    short_txt = memory_dir / "short.txt"
    short_log = memory_dir / SHORT_LOG_NAME
    if short_txt.is_file():
        if not short_log.is_file():
            try:
                short_txt.rename(short_log)
            except Exception:
                pass
        else:
            try:
                short_txt.unlink()
            except Exception:
                pass

    long_txt = memory_dir / "long.txt"
    long_log = memory_dir / LONG_LOG_NAME
    if long_txt.is_file():
        if not long_log.is_file():
            try:
                long_txt.rename(long_log)
            except Exception:
                pass
        else:
            try:
                long_txt.unlink()
            except Exception:
                pass


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


def prune_short_log(
    memory_dir: Path,
    state: MemoryState,
    max_tokens: int = MAX_SHORT_LOG_TOKENS,
) -> None:
    """Maintains a rolling context window of max_tokens in short.log."""
    short_log_path = memory_dir / SHORT_LOG_NAME
    if not short_log_path.is_file():
        return

    try:
        # Heuristic fast check: 100k tokens ~= 400KB. If file size < 350KB and tracked tokens <= max, skip.
        file_size = short_log_path.stat().st_size
        if file_size < 350_000 and state.short_log_tokens <= max_tokens:
            return
    except Exception:
        pass

    try:
        with open(short_log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return

    if not lines:
        state.data["short_log_tokens"] = 0
        state.save()
        return

    kept_lines = []
    current_tokens = 0
    window_start_seq = state.current_seq

    for line in reversed(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        rec_seq = None
        try:
            record = json.loads(line_clean)
            line_tokens = int(record.get("tokens", 0)) or max(1, len(line_clean) // 4)
            rec_seq = record.get("seq")
        except Exception:
            line_tokens = max(1, len(line_clean) // 4)

        if current_tokens + line_tokens > max_tokens and kept_lines:
            break

        if rec_seq:
            window_start_seq = rec_seq

        kept_lines.append(line)
        current_tokens += line_tokens

    if len(kept_lines) < len(lines):
        kept_lines.reverse()
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(memory_dir),
                delete=False,
            ) as f:
                temp_file = Path(f.name)
                for l in kept_lines:
                    f.write(l if l.endswith("\n") else l + "\n")
            temp_file.replace(short_log_path)
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    state.data["short_log_tokens"] = current_tokens
    state.data["window_start_seq"] = window_start_seq
    state.save()


def get_compaction_notice(seq: int, uncompacted_tokens: int) -> str:
    """Returns the standardized prompt instruction for updating long.log."""
    return (
        f"[MEMORY HOOK UPDATE NOTICE]: ~{uncompacted_tokens:,} tokens have accumulated since the "
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
    migrate_legacy_txt_files(memory_dir)

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
    event_tokens = int(event.get("tokens", 1))
    state.add_tokens(event_tokens)

    # Write to short.log and maintain rolling context window
    append_to_short_log(memory_dir, event)
    prune_short_log(memory_dir, state)
    state.save()

    # Check compaction condition (10k tokens)
    compaction_due = state.is_compaction_due()
    uncompacted_tokens = state.uncompacted_tokens

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
                notice = get_compaction_notice(seq, uncompacted_tokens)
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
            notice = get_compaction_notice(seq, uncompacted_tokens)
            # Writing to stderr in Claude Code passes feedback back to model context
            sys.stderr.write(f"\n{notice}\n")
            sys.stderr.flush()
            state.mark_compacted(seq)
        response = {"status": "ok"}

    else:
        if compaction_due:
            notice = get_compaction_notice(seq, uncompacted_tokens)
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
