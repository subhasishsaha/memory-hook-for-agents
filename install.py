#!/usr/bin/env python3
"""Standalone installer script for memory-hook.

Can be run directly via `python install.py` or downloaded and piped into python.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Try importing from local package or existing environment
install_hooks = None

try:
    from memory_hook.installer import install_hooks
except ImportError:
    # If in local clone
    current_dir = Path(__file__).resolve().parent
    if (current_dir / "memory_hook").is_dir():
        sys.path.insert(0, str(current_dir))
        try:
            from memory_hook.installer import install_hooks
        except ImportError:
            pass

if install_hooks is None:
    # Try installing from pip
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "memory-hook"],
            capture_output=True,
            check=False,
        )
        from memory_hook.installer import install_hooks
    except Exception:
        pass


def _standalone_install(target_dir: Path) -> dict:
    """Self-contained fallback installer when memory-hook package is not importable."""
    memory_dir = target_dir / ".memory"
    bin_dir = memory_dir / "bin"
    memory_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 0. Migrate any legacy .txt files (short.txt, long.txt) to .log
    short_txt = memory_dir / "short.txt"
    short_log = memory_dir / "short.log"
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
    long_log = memory_dir / "long.log"
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

    # 1. Initialize short.log
    if not short_log.exists():
        short_log.touch()

    # 2. Initialize long.log
    long_log = memory_dir / "long.log"
    if not long_log.exists():
        now_iso = datetime.now(timezone.utc).isoformat()
        long_log.write_text(
            f"# Project Memory & Knowledge Base\n"
            f"> Last Updated: {now_iso} | Provider: initial-setup | Synced Through Event: #0\n\n"
            f"## 1. Project Overview & Primary Goals\n"
            f"- **Mission**: Project root at {target_dir.name}\n\n"
            f"## 2. Technology Stack & Environment\n"
            f"- Auto-detected\n\n"
            f"## 3. Architecture & Core Decisions\n"
            f"- [x] Initialized cross-provider memory hook tracking.\n\n"
            f"## 4. Completed Work & Milestones\n"
            f"- [x] Repository configured with deterministic event logging.\n\n"
            f"## 5. Current State & Active Focus\n"
            f"- Initial project setup and memory initialization.\n\n"
            f"## 6. Next Immediate Steps\n"
            f"1. Execute project requirements.\n"
            f"2. Update `.memory/long.log` when prompted.\n",
            encoding="utf-8",
        )

    # 3. Initialize state.json
    state_file = memory_dir / "state.json"
    if not state_file.exists():
        state_data = {
            "version": 1,
            "current_seq": 0,
            "last_compacted_seq": 0,
            "total_events": 0,
            "last_updated_ts": None,
            "last_updated_provider": None,
            "total_lifetime_tokens": 0,
            "last_compacted_tokens": 0,
            "short_log_tokens": 0,
            "window_start_seq": 1,
        }
        state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")

    # 4. Generate standalone runner
    runner_script = bin_dir / "memory_hook_runner.py"
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
    with open(mem_dir / SHORT_LOG_NAME, "a", encoding="utf-8") as f:
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

if __name__ == "__main__":
    main()
'''
    compiled_runner = bin_dir / "memory_hook_runner.pyc"
    temp_source = bin_dir / "memory_hook_runner.py"

    # 1. Unset read-only attribute if already present from previous install
    if compiled_runner.exists():
        try:
            if os.name == "nt":
                import subprocess
                subprocess.run(["attrib", "-R", str(compiled_runner)], check=False, capture_output=True)
            else:
                compiled_runner.chmod(0o755)
        except Exception:
            pass

    # 2. Write source code temporarily
    temp_source.write_text(runner_code, encoding="utf-8")

    # 3. Compile bytecode locally on the user's system
    import py_compile
    py_compile.compile(str(temp_source), cfile=str(compiled_runner), doraise=True)

    # 4. Remove uncompiled source code so only the compiled bytecode is stored
    try:
        temp_source.unlink()
    except Exception:
        pass

    # 5. Lock compiled file with OS-level read-only permissions
    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["attrib", "+R", str(compiled_runner)], check=False, capture_output=True)
        else:
            compiled_runner.chmod(0o555)
    except Exception:
        pass

    # 5. Instructions and Provider configs
    proto_instructions = """# Universal Memory Hook Protocol (.memory/)

This repository is integrated with the **Cross-Provider Memory Hook**.
All agent activities (user requests, tool calls, tool responses, model outputs) are deterministically recorded in `.memory/short.log`, maintaining a rolling context window of **100,000 tokens** (100k tokens).

## Core Directives for All Agents:

1. **Context Restoration at Session Start**:
   - At the beginning of every session or task, read `.memory/long.log` first.
   - If needed, read the tail of `.memory/short.log` (last 20-30 lines) to catch up on the most recent actions taken by any previous agent (Claude, Antigravity, Codex, Cursor).
   - Never ask the user to re-explain what the project is about or what has already been built.

2. **Long-Term Memory Compaction**:
   - Whenever you receive a `[MEMORY HOOK UPDATE NOTICE]` indicating ~10,000 tokens have accumulated, review the uncompacted tail of `.memory/short.log` alongside the existing `.memory/long.log`.
   - Use your model tools (`edit_file` or `write_file`) to update `.memory/long.log`:
     - Update the header metadata: `Last Updated`, `Provider`, and `Synced Through Event: #<seq>`.
     - Update **Completed Work & Milestones** with newly finished items.
     - Update **Current State & Active Focus** with what you are working on right now.
     - Update **Next Immediate Steps** with pending tasks.
     - Preserve and append any newly established **Architecture & Core Decisions**.
     - Keep content concise, high-density, and structured.

3. **Security & Immutability Directives**:
   - The runner at `.memory/bin/memory_hook_runner.pyc` is locally compiled bytecode locked with OS read-only permissions.
   - Under NO circumstances should any agent attempt to edit, rewrite, replace, or delete files inside `.memory/bin/` or `.memory/state.json`.
   - All memory updates must strictly be applied to `.memory/long.log`.
"""

    def append_or_create_instructions(file_path: Path, full_new_content: str, append_protocol: str) -> None:
        if not file_path.exists():
            file_path.write_text(full_new_content.strip() + "\n", encoding="utf-8")
        else:
            existing = file_path.read_text(encoding="utf-8")
            if "Universal Memory Hook Protocol" not in existing:
                separator = "\n\n---\n\n" if existing.strip() else ""
                file_path.write_text(existing.rstrip() + separator + append_protocol.strip() + "\n", encoding="utf-8")

    root_abs = target_dir.resolve().as_posix()
    runner_abs = (target_dir / ".memory" / "bin" / "memory_hook_runner.pyc").resolve().as_posix()

    # Antigravity
    agents_dir = target_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agents_hooks = agents_dir / "hooks.json"
    existing_agents_hooks = {}
    if agents_hooks.is_file():
        try:
            existing_agents_hooks = json.loads(agents_hooks.read_text(encoding="utf-8"))
        except Exception:
            existing_agents_hooks = {}
    existing_agents_hooks["memory-hook"] = {
        "PreInvocation": [{"type": "command", "command": f'python "{runner_abs}" --provider antigravity --event PreInvocation --root "{root_abs}"'}],
        "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider antigravity --event PreToolUse --root "{root_abs}"'}]}],
        "PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider antigravity --event PostToolUse --root "{root_abs}"'}]}],
        "Stop": [{"type": "command", "command": f'python "{runner_abs}" --provider antigravity --event Stop --root "{root_abs}"'}]
    }
    agents_hooks.write_text(json.dumps(existing_agents_hooks, indent=2), encoding="utf-8")
    append_or_create_instructions(target_dir / "AGENTS.md", f"# Antigravity Agent Guidelines\n\n{proto_instructions}", proto_instructions)

    # Claude Code
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_settings = claude_dir / "settings.json"
    existing_claude_settings = {}
    if claude_settings.is_file():
        try:
            existing_claude_settings = json.loads(claude_settings.read_text(encoding="utf-8"))
        except Exception:
            existing_claude_settings = {}
    hooks = existing_claude_settings.get("hooks", {})
    hooks["UserPromptSubmit"] = [{"matcher": "", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"'}]}]
    hooks["PreToolUse"] = [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"'}]}]
    hooks["PostToolUse"] = [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"'}]}]
    hooks["Stop"] = [{"matcher": "", "hooks": [{"type": "command", "command": f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"'}]}]
    existing_claude_settings["hooks"] = hooks
    claude_settings.write_text(json.dumps(existing_claude_settings, indent=2), encoding="utf-8")
    append_or_create_instructions(target_dir / "CLAUDE.md", f"# Claude Code Guidelines\n\n{proto_instructions}", proto_instructions)

    # Codex
    codex_dir = target_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    codex_hooks = codex_dir / "hooks.json"
    existing_codex_hooks = {}
    if codex_hooks.is_file():
        try:
            existing_codex_hooks = json.loads(codex_hooks.read_text(encoding="utf-8"))
        except Exception:
            existing_codex_hooks = {}
    existing_codex_hooks["UserPromptSubmit"] = [{"type": "command", "command": f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"'}]
    existing_codex_hooks["PreToolUse"] = [{"type": "command", "command": f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"'}]
    existing_codex_hooks["PostToolUse"] = [{"type": "command", "command": f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"'}]
    existing_codex_hooks["Stop"] = [{"type": "command", "command": f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"'}]
    codex_hooks.write_text(json.dumps(existing_codex_hooks, indent=2), encoding="utf-8")
    append_or_create_instructions(target_dir / "CODEX.md", f"# Codex Guidelines\n\n{proto_instructions}", proto_instructions)

    # Cursor
    cursor_dir = target_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_hooks = cursor_dir / "hooks.json"
    existing_cursor_hooks = {}
    if cursor_hooks.is_file():
        try:
            existing_cursor_hooks = json.loads(cursor_hooks.read_text(encoding="utf-8"))
        except Exception:
            existing_cursor_hooks = {}
    existing_cursor_hooks["beforeSubmitPrompt"] = [{"command": f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"'}]
    existing_cursor_hooks["preToolUse"] = [{"command": f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"'}]
    existing_cursor_hooks["postToolUse"] = [{"command": f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"'}]
    existing_cursor_hooks["stop"] = [{"command": f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"'}]
    cursor_hooks.write_text(json.dumps(existing_cursor_hooks, indent=2), encoding="utf-8")
    append_or_create_instructions(target_dir / ".cursorrules", f"# Cursor Rules\n\n{proto_instructions}", proto_instructions)

    return {
        "antigravity": [".agents/hooks.json", "AGENTS.md"],
        "claude": [".claude/settings.json", "CLAUDE.md"],
        "codex": [".codex/hooks.json", "CODEX.md"],
        "cursor": [".cursor/hooks.json", ".cursorrules"],
    }


def main():
    target_dir = Path.cwd()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target_dir = Path(sys.argv[1]).resolve()

    print(f"Installing universal memory-hook into: {target_dir}")
    if install_hooks is not None:
        installed = install_hooks(target_dir, providers=["all"])
    else:
        installed = _standalone_install(target_dir)

    print("\n✓ Installation successful!")
    print("Configured providers:")
    for prov, files in installed.items():
        print(f"  • {prov}: {', '.join(files)}")

    print("\nDeterministic logging is active in .memory/short.log")
    print("Agent synthesis knowledge base is in .memory/long.log")


if __name__ == "__main__":
    main()
