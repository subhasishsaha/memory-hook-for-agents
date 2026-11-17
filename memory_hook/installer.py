"""Installer module to configure hooks and instructions across providers."""

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .constants import (
    BIN_DIR_NAME,
    LONG_LOG_NAME,
    MEMORY_DIR_NAME,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_CURSOR,
    RUNNER_SCRIPT_NAME,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
    SUPPORTED_PROVIDERS,
)
from .instructions import (
    LONG_LOG_TEMPLATE,
    get_agents_md_content,
    get_claude_md_content,
    get_codex_md_content,
    get_cursorrules_content,
)
from .state import MemoryState


def initialize_memory_directory(project_root: Path) -> Path:
    """Creates .memory directory, initializes short.log, long.log, state.json, and bin/."""
    memory_dir = project_root / MEMORY_DIR_NAME
    bin_dir = memory_dir / BIN_DIR_NAME
    memory_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initialize short.log if not exists
    short_log = memory_dir / SHORT_LOG_NAME
    if not short_log.exists():
        short_log.touch()

    # 2. Initialize long.log if not exists
    long_log = memory_dir / LONG_LOG_NAME
    if not long_log.exists():
        now_iso = datetime.now(timezone.utc).isoformat()
        initial_content = LONG_LOG_TEMPLATE.format(
            timestamp=now_iso,
            provider="initial-setup",
            seq=0,
            mission=f"Project root at {project_root.name}",
            use_case="General development with cross-provider AI agents",
            stack="Auto-detected",
            frameworks="Standard library",
        )
        with open(long_log, "w", encoding="utf-8") as f:
            f.write(initial_content)

    # 3. Initialize state.json
    state = MemoryState(memory_dir)
    state.save()

    # 4. Deploy self-contained portable runner script to .memory/bin/
    runner_target = bin_dir / RUNNER_SCRIPT_NAME
    # We generate a standalone script that does not require pip packaging
    generate_standalone_runner(runner_target)

    return memory_dir


def generate_standalone_runner(target_path: Path) -> None:
    """Generates a zero-dependency, self-contained Python runner in .memory/bin/."""
    runner_code = '''#!/usr/bin/env python3
"""Zero-dependency self-contained memory hook runner for .memory/"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR_NAME = ".memory"
SHORT_LOG_NAME = "short.log"
STATE_FILE_NAME = "state.json"
DEFAULT_COMPACT_INTERVAL = 10
MAX_PAYLOAD_STRING_LENGTH = 16384

def truncate_string(val, max_len=MAX_PAYLOAD_STRING_LENGTH):
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + f"\\n... [truncated {len(val) - max_len} chars]", True
        return val, False
    elif isinstance(val, dict):
        trunc = False
        new_d = {}
        for k, v in val.items():
            nv, tr = truncate_string(v, max_len)
            new_d[k] = nv
            if tr: trunc = True
        return new_d, trunc
    elif isinstance(val, list):
        trunc = False
        new_l = []
        for it in val:
            ni, tr = truncate_string(it, max_len)
            new_l.append(ni)
            if tr: trunc = True
        return new_l, trunc
    return val, False

def find_project_root():
    cur = Path(__file__).resolve().parent
    for p in [cur] + list(cur.parents):
        if (p / MEMORY_DIR_NAME).is_dir():
            return p
    return Path.cwd().resolve()

def load_state(mem_dir):
    sf = mem_dir / STATE_FILE_NAME
    if sf.is_file():
        try:
            with open(sf, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": 1, "current_seq": 0, "last_compacted_seq": 0, "total_events": 0}

def save_state(mem_dir, data):
    sf = mem_dir / STATE_FILE_NAME
    try:
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=str, default="")
    parser.add_argument("--provider", type=str, default="")
    args = parser.parse_args()

    raw_in = ""
    try:
        if not sys.stdin.isatty():
            raw_in = sys.stdin.read()
    except Exception:
        pass

    raw_payload = {}
    if raw_in:
        try:
            raw_payload = json.loads(raw_in)
        except Exception:
            raw_payload = {"raw": raw_in}

    root = find_project_root()
    mem_dir = root / MEMORY_DIR_NAME
    mem_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(mem_dir)
    seq = state.get("current_seq", 0) + 1
    state["current_seq"] = seq
    state["total_events"] = state.get("total_events", 0) + 1
    state["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
    if args.provider:
        state["last_updated_provider"] = args.provider

    # Determine event type
    evt = args.event.lower()
    event_type = "user_request"
    payload = {}
    is_trunc = False

    if "pretool" in evt:
        event_type = "tool_call"
        tc = raw_payload.get("toolCall") or raw_payload.get("tool_input") or {}
        payload = {"tool": raw_payload.get("tool_name") or raw_payload.get("name") or "tool", "args": tc}
    elif "posttool" in evt:
        event_type = "tool_result"
        payload = {
            "status": "error" if raw_payload.get("error") else "success",
            "output": raw_payload.get("tool_response") or raw_payload.get("result") or raw_payload.get("output") or "",
            "error": raw_payload.get("error")
        }
    elif "stop" in evt or "response" in evt:
        event_type = "model_response"
        payload = {"content": raw_payload.get("response") or raw_payload.get("last_assistant_message") or ""}
    else:
        event_type = "user_request"
        payload = {"content": raw_payload.get("lastUserInput") or raw_payload.get("prompt") or raw_payload.get("text") or raw_in}

    payload, is_trunc = truncate_string(payload)

    record = {
        "seq": seq,
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider or "generic",
        "session_id": raw_payload.get("conversationId") or raw_payload.get("sessionId") or "unknown",
        "turn_id": raw_payload.get("stepIdx") or raw_payload.get("invocationNum"),
        "type": event_type,
        "payload": payload,
        "truncated": is_trunc
    }

    # Append to short.log
    with open(mem_dir / SHORT_LOG_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\\n")

    # Compaction check
    last_compact = state.get("last_compacted_seq", 0)
    compaction_due = (seq - last_compact) >= DEFAULT_COMPACT_INTERVAL
    notice = (
        f"[MEMORY HOOK UPDATE NOTICE]: {seq - last_compact} actions elapsed since last long.log update. "
        f"Action Required: Review uncompacted entries in .memory/short.log and update .memory/long.log."
    )

    resp = {}
    if args.provider == "antigravity":
        if args.event == "PreToolUse":
            resp = {"decision": "allow"}
        elif args.event in ["PreInvocation", "SessionStart"]:
            if compaction_due:
                resp = {"injectSteps": [{"systemMessage": {"systemMessage": notice}}]}
                state["last_compacted_seq"] = seq
            else:
                resp = {}
        elif args.event == "Stop":
            resp = {"decision": "allow"}
    else:
        if compaction_due:
            sys.stderr.write(f"\\n{notice}\\n")
            sys.stderr.flush()
            state["last_compacted_seq"] = seq
        resp = {"status": "ok"}

    save_state(mem_dir, state)
    sys.stdout.write(json.dumps(resp))
    sys.stdout.flush()

if __name__ == "__main__":
    main()
'''
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(runner_code)
    try:
        # Make executable on Unix/Mac
        target_path.chmod(0o755)
    except Exception:
        pass


def install_antigravity(project_root: Path) -> List[Path]:
    """Configures Antigravity .agents/hooks.json and AGENTS.md."""
    agents_dir = project_root / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = agents_dir / "hooks.json"

    runner_rel = ".memory/bin/memory_hook_runner.py"

    existing_hooks: Dict[str, Any] = {}
    if hooks_file.is_file():
        try:
            with open(hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        except Exception:
            existing_hooks = {}

    memory_hook_config = {
        "PreInvocation": [
            {
                "type": "command",
                "command": f"python {runner_rel} --provider antigravity --event PreInvocation"
            }
        ],
        "PreToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python {runner_rel} --provider antigravity --event PreToolUse"
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python {runner_rel} --provider antigravity --event PostToolUse"
                    }
                ]
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f"python {runner_rel} --provider antigravity --event Stop"
            }
        ]
    }

    existing_hooks["memory-hook"] = memory_hook_config
    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    # Instructions
    agents_md = project_root / "AGENTS.md"
    content = get_agents_md_content()
    if not agents_md.exists():
        with open(agents_md, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(agents_md, "r", encoding="utf-8") as f:
            current = f.read()
        if "Universal Memory Hook Protocol" not in current:
            with open(agents_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + content)

    return [hooks_file, agents_md]


def install_claude(project_root: Path) -> List[Path]:
    """Configures Claude Code .claude/settings.json and CLAUDE.md."""
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_file = claude_dir / "settings.json"

    runner_rel = ".memory/bin/memory_hook_runner.py"

    existing_settings: Dict[str, Any] = {}
    if settings_file.is_file():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                existing_settings = json.load(f)
        except Exception:
            existing_settings = {}

    hooks = existing_settings.get("hooks", {})

    hooks["UserPromptSubmit"] = [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python {runner_rel} --provider claude-code --event UserPromptSubmit"
                }
            ]
        }
    ]
    hooks["PreToolUse"] = [
        {
            "matcher": ".*",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python {runner_rel} --provider claude-code --event PreToolUse"
                }
            ]
        }
    ]
    hooks["PostToolUse"] = [
        {
            "matcher": ".*",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python {runner_rel} --provider claude-code --event PostToolUse"
                }
            ]
        }
    ]
    hooks["Stop"] = [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python {runner_rel} --provider claude-code --event Stop"
                }
            ]
        }
    ]

    existing_settings["hooks"] = hooks
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(existing_settings, f, indent=2, ensure_ascii=False)

    # Instructions
    claude_md = project_root / "CLAUDE.md"
    content = get_claude_md_content()
    if not claude_md.exists():
        with open(claude_md, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(claude_md, "r", encoding="utf-8") as f:
            current = f.read()
        if "Universal Memory Hook Protocol" not in current:
            with open(claude_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + content)

    return [settings_file, claude_md]


def install_codex(project_root: Path) -> List[Path]:
    """Configures OpenAI Codex .codex/hooks.json and CODEX.md."""
    codex_dir = project_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = codex_dir / "hooks.json"

    runner_rel = ".memory/bin/memory_hook_runner.py"

    existing_hooks: Dict[str, Any] = {}
    if hooks_file.is_file():
        try:
            with open(hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        except Exception:
            existing_hooks = {}

    existing_hooks["UserPromptSubmit"] = [
        {"type": "command", "command": f"python {runner_rel} --provider codex --event UserPromptSubmit"}
    ]
    existing_hooks["PreToolUse"] = [
        {"type": "command", "command": f"python {runner_rel} --provider codex --event PreToolUse"}
    ]
    existing_hooks["PostToolUse"] = [
        {"type": "command", "command": f"python {runner_rel} --provider codex --event PostToolUse"}
    ]
    existing_hooks["Stop"] = [
        {"type": "command", "command": f"python {runner_rel} --provider codex --event Stop"}
    ]

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    codex_md = project_root / "CODEX.md"
    content = get_codex_md_content()
    if not codex_md.exists():
        with open(codex_md, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(codex_md, "r", encoding="utf-8") as f:
            current = f.read()
        if "Universal Memory Hook Protocol" not in current:
            with open(codex_md, "a", encoding="utf-8") as f:
                f.write("\n\n" + content)

    return [hooks_file, codex_md]


def install_cursor(project_root: Path) -> List[Path]:
    """Configures Cursor .cursor/hooks.json and .cursorrules."""
    cursor_dir = project_root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = cursor_dir / "hooks.json"

    runner_rel = ".memory/bin/memory_hook_runner.py"

    existing_hooks: Dict[str, Any] = {}
    if hooks_file.is_file():
        try:
            with open(hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        except Exception:
            existing_hooks = {}

    existing_hooks["beforeSubmitPrompt"] = [
        {"command": f"python {runner_rel} --provider cursor --event beforeSubmitPrompt"}
    ]
    existing_hooks["preToolUse"] = [
        {"command": f"python {runner_rel} --provider cursor --event preToolUse"}
    ]
    existing_hooks["postToolUse"] = [
        {"command": f"python {runner_rel} --provider cursor --event postToolUse"}
    ]
    existing_hooks["stop"] = [
        {"command": f"python {runner_rel} --provider cursor --event stop"}
    ]

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    cursorrules = project_root / ".cursorrules"
    content = get_cursorrules_content()
    if not cursorrules.exists():
        with open(cursorrules, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(cursorrules, "r", encoding="utf-8") as f:
            current = f.read()
        if "Universal Memory Hook Protocol" not in current:
            with open(cursorrules, "a", encoding="utf-8") as f:
                f.write("\n\n" + content)

    return [hooks_file, cursorrules]


def install_hooks(
    project_root: Optional[Path] = None,
    providers: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Installs memory-hook into the designated project repository."""
    root = (project_root or Path.cwd()).resolve()
    initialize_memory_directory(root)

    installed_files: Dict[str, List[str]] = {}
    targets = providers or ["all"]
    if "all" in targets:
        targets = [PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_CURSOR]

    for p in targets:
        p_clean = p.lower().strip()
        if p_clean in [PROVIDER_ANTIGRAVITY, "gemini"]:
            files = install_antigravity(root)
            installed_files[PROVIDER_ANTIGRAVITY] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CLAUDE, "claude"]:
            files = install_claude(root)
            installed_files[PROVIDER_CLAUDE] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CODEX, "openai"]:
            files = install_codex(root)
            installed_files[PROVIDER_CODEX] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CURSOR, "cursor"]:
            files = install_cursor(root)
            installed_files[PROVIDER_CURSOR] = [str(f.relative_to(root)) for f in files]

    return installed_files
