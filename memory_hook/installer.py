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
    RUNNER_COMPILED_NAME,
    RUNNER_SCRIPT_NAME,
    RUNNER_SOURCE_NAME,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
    SUPPORTED_PROVIDERS,
)
from .instructions import (
    AGENT_MEMORY_INSTRUCTIONS,
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

    # 0. Migrate any legacy .txt files (short.txt, long.txt) to .log
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

    # 1. Initialize short.log if not exists
    if not short_log.exists():
        short_log.touch()

    # 2. Initialize long.log if not exists
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
    # Clean up legacy uncompiled source if present
    legacy_source = bin_dir / RUNNER_SOURCE_NAME
    if legacy_source.is_file():
        try:
            legacy_source.unlink()
        except Exception:
            pass

    runner_target = bin_dir / RUNNER_SCRIPT_NAME
    # We generate and compile on the user system so only the compiled bytecode is stored
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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR_NAME = ".memory"
SHORT_LOG_NAME = "short.log"
LONG_LOG_NAME = "long.log"
STATE_FILE_NAME = "state.json"
DEFAULT_COMPACT_TOKEN_INTERVAL = 10000
MAX_SHORT_LOG_TOKENS = 100000
MAX_PAYLOAD_STRING_LENGTH = 16384

def estimate_tokens(val):
    if val is None: return 0
    if isinstance(val, (int, float, bool)): return 1
    if isinstance(val, str): return max(1, len(val) // 4) if val else 0
    try:
        s = json.dumps(val, ensure_ascii=False)
        return max(1, len(s) // 4) if s else 0
    except Exception:
        return max(1, len(str(val)) // 4)

def migrate_legacy_txt_files(mem_dir):
    st = mem_dir / "short.txt"
    sl = mem_dir / SHORT_LOG_NAME
    if st.is_file():
        if not sl.is_file():
            try: st.rename(sl)
            except Exception: pass
        else:
            try: st.unlink()
            except Exception: pass
    lt = mem_dir / "long.txt"
    ll = mem_dir / "long.log"
    if lt.is_file():
        if not ll.is_file():
            try: lt.rename(ll)
            except Exception: pass
        else:
            try: lt.unlink()
            except Exception: pass

def prune_short_log(mem_dir, state, max_tokens=MAX_SHORT_LOG_TOKENS):
    sl = mem_dir / SHORT_LOG_NAME
    if not sl.is_file(): return
    try:
        if sl.stat().st_size < 350000 and state.get("short_log_tokens", 0) <= max_tokens:
            return
    except Exception: pass
    try:
        with open(sl, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception: return
    if not lines: return
    kept = []
    tokens = 0
    w_start = state.get("current_seq", 1)
    for l in reversed(lines):
        lc = l.strip()
        if not lc: continue
        lt = 0
        r_seq = None
        try:
            r = json.loads(lc)
            lt = int(r.get("tokens", 0)) or max(1, len(lc) // 4)
            r_seq = r.get("seq")
        except Exception:
            lt = max(1, len(lc) // 4)
        if tokens + lt > max_tokens and kept:
            break
        if r_seq: w_start = r_seq
        kept.append(l)
        tokens += lt
    if len(kept) < len(lines):
        kept.reverse()
        tf = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(mem_dir), delete=False) as f:
                tf = Path(f.name)
                for l in kept:
                    f.write(l if l.endswith("\\n") else l + "\\n")
            tf.replace(sl)
        finally:
            if tf and tf.exists():
                try: tf.unlink()
                except Exception: pass
    state["short_log_tokens"] = tokens
    state["window_start_seq"] = w_start

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

def extract_model_response(raw_payload):
    for k in ["response", "last_assistant_message", "last_response", "assistant_message", "text", "output"]:
        val = raw_payload.get(k)
        if isinstance(val, str) and val.strip():
            return val
        elif isinstance(val, dict) and val.get("content"):
            return str(val["content"])

    transcript_paths = []
    tp = raw_payload.get("transcriptPath")
    if tp:
        p = Path(tp)
        transcript_paths.append(p.parent / "transcript_full.jsonl")
        transcript_paths.append(p)

    cid = raw_payload.get("conversationId") or raw_payload.get("sessionId")
    if cid:
        home = Path.home()
        for variant in ["antigravity-cli", "antigravity", "antigravity-ide"]:
            log_dir = home / ".gemini" / variant / "brain" / cid / ".system_generated" / "logs"
            transcript_paths.append(log_dir / "transcript_full.jsonl")
            transcript_paths.append(log_dir / "transcript.jsonl")

    for tp_cand in transcript_paths:
        if tp_cand.is_file():
            try:
                with open(tp_cand, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    for line in reversed(lines[-100:]):
                        item = json.loads(line)
                        if (item.get("type") == "PLANNER_RESPONSE" or item.get("source") == "MODEL") and item.get("content"):
                            return str(item["content"])
            except Exception:
                continue
    return ""

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
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--event", type=str, default="")
        parser.add_argument("--provider", type=str, default="")
        parser.add_argument("--root", type=str, default="")
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

        root = Path(args.root).resolve() if args.root else find_project_root()
        mem_dir = root / MEMORY_DIR_NAME
        mem_dir.mkdir(parents=True, exist_ok=True)
        migrate_legacy_txt_files(mem_dir)

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
            payload = {"content": extract_model_response(raw_payload)}
        else:
            event_type = "user_request"
            payload = {"content": raw_payload.get("lastUserInput") or raw_payload.get("prompt") or raw_payload.get("text") or raw_in}

        payload, is_trunc = truncate_string(payload)
        event_tokens = max(1, estimate_tokens(payload))
        state["total_lifetime_tokens"] = state.get("total_lifetime_tokens", 0) + event_tokens
        state["short_log_tokens"] = state.get("short_log_tokens", 0) + event_tokens

        record = {
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": args.provider or "generic",
            "session_id": raw_payload.get("conversationId") or raw_payload.get("sessionId") or "unknown",
            "turn_id": raw_payload.get("stepIdx") or raw_payload.get("invocationNum"),
            "type": event_type,
            "payload": payload,
            "tokens": event_tokens,
            "truncated": is_trunc
        }

        # Append to short.log and maintain rolling context window
        with open(mem_dir / SHORT_LOG_NAME, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\\n")
        prune_short_log(mem_dir, state)

        # Compaction check (10k tokens)
        uncompacted = state["total_lifetime_tokens"] - state.get("last_compacted_tokens", 0)
        compaction_due = uncompacted >= DEFAULT_COMPACT_TOKEN_INTERVAL
        notice = (
            f"[MEMORY HOOK UPDATE NOTICE]: ~{uncompacted:,} tokens have accumulated since the "
            f"last long-term memory update (Current Event: #{seq}). "
            f"Action Required: Review uncompacted entries in .memory/short.log and update .memory/long.log."
        )

        resp = {}
        if args.provider == "antigravity":
            if args.event == "PreToolUse":
                resp = {"decision": "allow"}
            elif args.event in ["PreInvocation", "SessionStart"]:
                if compaction_due:
                    resp = {"injectSteps": [{"systemMessage": {"systemMessage": notice}}]}
                    state["last_compacted_tokens"] = state["total_lifetime_tokens"]
                    state["last_compacted_seq"] = seq
                else:
                    resp = {}
            elif args.event == "Stop":
                resp = {"decision": "allow"}
        else:
            if compaction_due:
                sys.stderr.write(f"\\n{notice}\\n")
                sys.stderr.flush()
                state["last_compacted_tokens"] = state["total_lifetime_tokens"]
                state["last_compacted_seq"] = seq
            resp = {"status": "ok"}

        save_state(mem_dir, state)
        sys.stdout.write(json.dumps(resp))
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"memory-hook runner warning: {exc}\\n")
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"status": "ok", "decision": "allow"}))
        sys.stdout.flush()
        sys.exit(0)

if __name__ == "__main__":
    main()
'''
    bin_dir = target_path.parent
    compiled_path = bin_dir / RUNNER_COMPILED_NAME
    temp_source_path = bin_dir / RUNNER_SOURCE_NAME

    # 1. Unset read-only attribute if already present from a previous install
    if compiled_path.exists():
        try:
            if os.name == "nt":
                import subprocess
                subprocess.run(["attrib", "-R", str(compiled_path)], check=False, capture_output=True)
            else:
                compiled_path.chmod(0o755)
        except Exception:
            pass

    # 2. Write source code temporarily
    with open(temp_source_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    # 3. Compile bytecode locally on the user's system
    import py_compile
    py_compile.compile(str(temp_source_path), cfile=str(compiled_path), doraise=True)

    # 4. Remove uncompiled source code so only the compiled bytecode is stored
    try:
        temp_source_path.unlink()
    except Exception:
        pass

    # 5. Lock compiled file with OS-level read-only permissions
    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["attrib", "+R", str(compiled_path)], check=False, capture_output=True)
        else:
            compiled_path.chmod(0o555)
    except Exception:
        pass


def append_or_create_instructions(
    file_path: Path,
    full_new_content: str,
    append_protocol: str = AGENT_MEMORY_INSTRUCTIONS,
) -> None:
    """Creates instruction file or appends memory protocol at the bottom as separate section."""
    if not file_path.exists():
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_new_content.strip() + "\n")
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            existing = f.read()
        if "Universal Memory Hook Protocol" not in existing:
            separator = "\n\n---\n\n" if existing.strip() else ""
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(separator + append_protocol.strip() + "\n")


def quote_if_needed(path_str: str) -> str:
    """Wraps path in double quotes only if it contains whitespace."""
    return f'"{path_str}"' if " " in path_str else path_str


def install_antigravity(project_root: Path) -> List[Path]:
    """Configures Antigravity .agents/hooks.json and AGENTS.md."""
    agents_dir = project_root / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = agents_dir / "hooks.json"

    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()
    r_arg = quote_if_needed(runner_abs)
    rt_arg = quote_if_needed(root_abs)

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
                "command": f'python {r_arg} --provider antigravity --event PreInvocation --root {rt_arg}'
            }
        ],
        "PreToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python {r_arg} --provider antigravity --event PreToolUse --root {rt_arg}'
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
                        "command": f'python {r_arg} --provider antigravity --event PostToolUse --root {rt_arg}'
                    }
                ]
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f'python {r_arg} --provider antigravity --event Stop --root {rt_arg}'
            }
        ]
    }

    existing_hooks["memory-hook"] = memory_hook_config
    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    # Instructions
    agents_md = project_root / "AGENTS.md"
    append_or_create_instructions(agents_md, get_agents_md_content())
    return [hooks_file, agents_md]


def install_claude(project_root: Path) -> List[Path]:
    """Configures Claude Code .claude/settings.json and CLAUDE.md."""
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_file = claude_dir / "settings.json"

    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

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
                    "command": f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"'
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
                    "command": f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"'
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
                    "command": f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"'
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
                    "command": f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"'
                }
            ]
        }
    ]

    existing_settings["hooks"] = hooks
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(existing_settings, f, indent=2, ensure_ascii=False)

    # Instructions
    claude_md = project_root / "CLAUDE.md"
    append_or_create_instructions(claude_md, get_claude_md_content())
    return [settings_file, claude_md]


def install_codex(project_root: Path) -> List[Path]:
    """Configures OpenAI Codex .codex/hooks.json and CODEX.md."""
    codex_dir = project_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = codex_dir / "hooks.json"

    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    existing_hooks: Dict[str, Any] = {}
    if hooks_file.is_file():
        try:
            with open(hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        except Exception:
            existing_hooks = {}

    existing_hooks["UserPromptSubmit"] = [
        {"type": "command", "command": f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"'}
    ]
    existing_hooks["PreToolUse"] = [
        {"type": "command", "command": f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"'}
    ]
    existing_hooks["PostToolUse"] = [
        {"type": "command", "command": f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"'}
    ]
    existing_hooks["Stop"] = [
        {"type": "command", "command": f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"'}
    ]

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    codex_md = project_root / "CODEX.md"
    append_or_create_instructions(codex_md, get_codex_md_content())
    return [hooks_file, codex_md]


def install_cursor(project_root: Path) -> List[Path]:
    """Configures Cursor .cursor/hooks.json and .cursorrules."""
    cursor_dir = project_root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = cursor_dir / "hooks.json"

    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    existing_hooks: Dict[str, Any] = {}
    if hooks_file.is_file():
        try:
            with open(hooks_file, "r", encoding="utf-8") as f:
                existing_hooks = json.load(f)
        except Exception:
            existing_hooks = {}

    existing_hooks["beforeSubmitPrompt"] = [
        {"command": f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"'}
    ]
    existing_hooks["preToolUse"] = [
        {"command": f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"'}
    ]
    existing_hooks["postToolUse"] = [
        {"command": f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"'}
    ]
    existing_hooks["stop"] = [
        {"command": f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"'}
    ]

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    cursorrules = project_root / ".cursorrules"
    append_or_create_instructions(cursorrules, get_cursorrules_content())
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
