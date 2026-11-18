#!/usr/bin/env python3
"""Standalone installer script for memory-hook.

Can be run directly via `python install.py` or downloaded and piped into python.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Try importing from local package or existing environment
install_hooks = None

try:
    from memory_hook.installer import install_hooks
except ImportError:
    current_dir = Path(__file__).resolve().parent
    if (current_dir / "memory_hook").is_dir():
        sys.path.insert(0, str(current_dir))
        try:
            from memory_hook.installer import install_hooks
        except ImportError:
            pass

if install_hooks is None:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "memory-hook"],
            capture_output=True,
            check=False,
        )
        from memory_hook.installer import install_hooks
    except Exception:
        pass


def _standalone_install(
    target_dir: Path,
    providers: Optional[List[str]] = None,
    dry_run: bool = False,
) -> dict:
    """Self-contained fallback installer when memory-hook package is not importable."""
    memory_dir = target_dir / ".memory"
    bin_dir = memory_dir / "bin"
    if dry_run:
        return {"dry_run": True}

    memory_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # Migrate legacy .txt files
    short_txt = memory_dir / "short.txt"
    short_log = memory_dir / "short.log"
    if short_txt.is_file():
        if not short_log.is_file():
            try: short_txt.rename(short_log)
            except Exception: pass
        else:
            try: short_txt.unlink()
            except Exception: pass

    long_txt = memory_dir / "long.txt"
    long_log = memory_dir / "long.log"
    if long_txt.is_file():
        if not long_log.is_file():
            try: long_txt.rename(long_log)
            except Exception: pass
        else:
            try: long_txt.unlink()
            except Exception: pass

    if not short_log.exists():
        short_log.touch()

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

    state_file = memory_dir / "state.json"
    state_data = {
        "version": 2,
        "current_seq": 0,
        "last_compacted_seq": 0,
        "total_events": 0,
        "last_updated_ts": None,
        "last_updated_provider": None,
        "total_lifetime_tokens": 0,
        "last_compacted_tokens": 0,
        "short_log_tokens": 0,
        "window_start_seq": 1,
        "pending_compaction": None,
        "runner_sha256": None,
    }
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state_data.update(loaded)
        except Exception:
            pass

    # Synchronized Standalone runner
    runner_code = r'''#!/usr/bin/env python3
"""Zero-dependency self-contained memory hook runner for .memory/ with locking & redaction."""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR_NAME = ".memory"
SHORT_LOG_NAME = "short.log"
LONG_LOG_NAME = "long.log"
STATE_FILE_NAME = "state.json"
DEFAULT_COMPACT_TOKEN_INTERVAL = 10000
MAX_SHORT_LOG_TOKENS = 100000
MAX_PAYLOAD_STRING_LENGTH = 16384

REDACTION_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED:PRIVATE_KEY]"),
    (re.compile(r"\\bsk-[a-zA-Z0-9_\\-]{20,}\\b"), "[REDACTED:API_KEY]"),
    (re.compile(r"\\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\\b"), "[REDACTED:GITHUB_TOKEN]"),
    (re.compile(r"\\bgithub_pat_[a-zA-Z0-9_]{60,}\\b"), "[REDACTED:GITHUB_PAT]"),
    (re.compile(r"\\bAIza[0-9A-Za-z\\-_]{35}\\b"), "[REDACTED:GOOGLE_API_KEY]"),
    (re.compile(r"\\bAKIA[0-9A-Z]{16}\\b"), "[REDACTED:AWS_KEY_ID]"),
    (re.compile(r"\\bxox[baprs]-[0-9a-zA-Z]{10,}\\b"), "[REDACTED:SLACK_TOKEN]"),
    (re.compile(r"(?i)\\bBearer\\s+[a-zA-Z0-9_\\-\\..]{20,}\\b"), "Bearer [REDACTED:BEARER_TOKEN]"),
    (re.compile(r"""(?i)\\b(api[_-]?key|secret|password|passwd|auth[_-]?token|access[_-]?token)\\s*[:=]\\s*["']?([^\\s"']{8,})["']?"""), r"\\1=[REDACTED:SECRET]"),
]

def redact_val(val):
    if isinstance(val, str):
        res = val
        for pat, rep in REDACTION_PATTERNS:
            res = pat.sub(rep, res)
        return res
    elif isinstance(val, dict):
        new_d = {}
        for k, v in val.items():
            if any(s in str(k).lower() for s in ["password", "secret", "api_key", "token"]) and isinstance(v, str):
                new_d[k] = "[REDACTED:SECRET]"
            else:
                new_d[k] = redact_val(v)
        return new_d
    elif isinstance(val, list):
        return [redact_val(x) for x in val]
    return val

class StandaloneLock:
    def __init__(self, lock_path, timeout=5.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.f = None
    def __enter__(self):
        start = time.monotonic()
        while True:
            try:
                self.f = open(self.lock_path, "a+", encoding="utf-8")
                if sys.platform == "win32":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (IOError, OSError):
                if self.f:
                    try: self.f.close()
                    except Exception: pass
                    self.f = None
                if time.monotonic() - start >= self.timeout:
                    return self
                time.sleep(0.02)
    def __exit__(self, et, ev, tb):
        if self.f:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
            except Exception: pass
            finally:
                try: self.f.close()
                except Exception: pass
                self.f = None

def estimate_tokens(val):
    if val is None: return 0
    if isinstance(val, (int, float, bool)): return 1
    if isinstance(val, str): return max(1, len(val) // 4) if val else 0
    try:
        s = json.dumps(val, ensure_ascii=False)
        return max(1, len(s) // 4) if s else 0
    except Exception:
        return max(1, len(str(val)) // 4)

def find_project_root():
    curr = Path.cwd().resolve()
    for p in [curr] + list(curr.parents):
        if (p / MEMORY_DIR_NAME).is_dir() or (p / ".git").exists():
            return p
    return curr

def load_state(mem_dir):
    sf = mem_dir / STATE_FILE_NAME
    default_state = {
        "version": 2, "current_seq": 0, "last_compacted_seq": 0,
        "total_events": 0, "total_lifetime_tokens": 0, "last_compacted_tokens": 0,
        "short_log_tokens": 0, "window_start_seq": 1, "pending_compaction": None
    }
    if sf.is_file():
        try:
            with open(sf, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict):
                    default_state.update(d)
                    return default_state
        except Exception:
            pass
    return default_state

def save_state(mem_dir, state):
    sf = mem_dir / STATE_FILE_NAME
    tf = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(mem_dir), delete=False) as f:
            tf = Path(f.name)
            json.dump(state, f, indent=2, ensure_ascii=False)
        tf.replace(sf)
    except Exception:
        try:
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception: pass
    finally:
        if tf and tf.exists():
            try: tf.unlink()
            except Exception: pass

def check_long_log_sync(mem_dir, state):
    pc = state.get("pending_compaction")
    if not pc or not isinstance(pc, dict) or pc.get("status") != "pending":
        return False
    ll_path = mem_dir / LONG_LOG_NAME
    if not ll_path.is_file():
        return False
    try:
        with open(ll_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)
        match = re.search(r"Synced Through Event:\\s*#(\\d+)", content)
        requested_seq = int(pc.get("requested_at_seq", 0))
        if match:
            synced_seq = int(match.group(1))
            if synced_seq >= requested_seq:
                state["last_compacted_seq"] = synced_seq
                state["last_compacted_tokens"] = state.get("total_lifetime_tokens", 0)
                state["pending_compaction"] = None
                return True
    except Exception: pass
    return False

def truncate_string(val, max_len=MAX_PAYLOAD_STRING_LENGTH):
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + f"\\n... [truncated {len(val)-max_len} characters]", True
        return val, False
    elif isinstance(val, dict):
        trunc = False
        new_d = {}
        for k, v in val.items():
            nv, w = truncate_string(v, max_len)
            new_d[k] = nv
            if w: trunc = True
        return new_d, trunc
    elif isinstance(val, list):
        trunc = False
        new_l = []
        for x in val:
            nx, w = truncate_string(x, max_len)
            new_l.append(nx)
            if w: trunc = True
        return new_l, trunc
    return val, False

def prune_short_log(mem_dir, state):
    sl = mem_dir / SHORT_LOG_NAME
    if not sl.is_file(): return
    try:
        with open(sl, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception: return
    if not lines: return

    kept = []
    tokens = 0
    start_seq = state.get("current_seq", 0)
    pc = state.get("pending_compaction")
    pending_seq = pc.get("requested_at_seq") if pc else None

    for line in reversed(lines):
        line_clean = line.strip()
        if not line_clean: continue
        rec_seq = None
        try:
            record = json.loads(line_clean)
            lt = int(record.get("tokens", 0)) or max(1, len(line_clean) // 4)
            rec_seq = record.get("seq")
        except Exception:
            lt = max(1, len(line_clean) // 4)

        if tokens + lt > MAX_SHORT_LOG_TOKENS and kept:
            if pending_seq is None or (rec_seq is not None and rec_seq > pending_seq):
                break
        if rec_seq is not None:
            start_seq = rec_seq
        kept.append(line)
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
    state["window_start_seq"] = start_seq

def main():
    try:
        if hasattr(sys.stdin, "reconfigure"): sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--event", type=str, default="")
        parser.add_argument("--provider", type=str, default="")
        parser.add_argument("--root", type=str, default="")
        args = parser.parse_args()

        raw_in = ""
        try:
            if not sys.stdin.isatty(): raw_in = sys.stdin.read()
        except Exception: pass

        raw_payload = {}
        if raw_in:
            try: raw_payload = json.loads(raw_in)
            except Exception: raw_payload = {"raw": raw_in}

        root = Path(args.root).resolve() if args.root else find_project_root()
        mem_dir = root / MEMORY_DIR_NAME
        mem_dir.mkdir(parents=True, exist_ok=True)

        resp = {}
        with StandaloneLock(mem_dir / ".lock", timeout=5.0):
            state = load_state(mem_dir)
            check_long_log_sync(mem_dir, state)

            seq = state.get("current_seq", 0) + 1
            state["current_seq"] = seq
            state["total_events"] = state.get("total_events", 0) + 1
            state["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
            if args.provider: state["last_updated_provider"] = args.provider

            evt = args.event.lower()
            event_type = "user_request"
            payload = {}
            is_trunc = False

            if "pretool" in evt:
                event_type = "tool_call"
                tc = raw_payload.get("toolCall") or raw_payload.get("tool_input") or {}
                payload = {"call_id": f"call_{seq}", "tool_name": raw_payload.get("tool_name") or raw_payload.get("name") or "tool", "args": tc}
            elif "posttool" in evt:
                event_type = "tool_result"
                payload = {
                    "call_id": f"call_{seq}",
                    "tool_name": raw_payload.get("tool_name") or raw_payload.get("name") or "tool",
                    "status": "error" if raw_payload.get("error") else "success",
                    "content": raw_payload.get("tool_response") or raw_payload.get("result") or raw_payload.get("output") or "",
                    "error": raw_payload.get("error")
                }
            elif "stop" in evt or "response" in evt:
                event_type = "model_response"
                payload = {"content": str(raw_payload.get("content") or raw_payload.get("response") or "")}
            else:
                event_type = "user_request"
                payload = {"content": str(raw_payload.get("lastUserInput") or raw_payload.get("prompt") or raw_payload.get("text") or raw_in)}

            payload = redact_val(payload)
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

            with open(mem_dir / SHORT_LOG_NAME, "a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\\n")
            prune_short_log(mem_dir, state)

            uncompacted = state["total_lifetime_tokens"] - state.get("last_compacted_tokens", 0)
            if uncompacted >= DEFAULT_COMPACT_TOKEN_INTERVAL and not state.get("pending_compaction"):
                state["pending_compaction"] = {
                    "status": "pending",
                    "requested_at_seq": seq,
                    "requested_tokens": uncompacted,
                    "requested_at": datetime.now(timezone.utc).isoformat()
                }

            has_pending = bool(state.get("pending_compaction"))
            notice = (
                f"[MEMORY HOOK UPDATE NOTICE]: ~{uncompacted:,} tokens have accumulated since the "
                f"last long-term memory update (Current Event: #{seq}). "
                f"Action Required: Review uncompacted entries in .memory/short.log and update .memory/long.log."
            )

            if args.provider == "antigravity":
                if args.event == "PreToolUse":
                    resp = {"decision": "allow"}
                elif args.event in ["PreInvocation", "SessionStart"]:
                    resp = {"injectSteps": [{"systemMessage": {"systemMessage": notice}}]} if has_pending else {}
                elif args.event == "Stop":
                    resp = {"decision": "allow"}
            else:
                is_start = "prompt" in evt or "submit" in evt or not evt
                if has_pending and is_start:
                    sys.stderr.write(f"\\n{notice}\\n")
                    sys.stderr.flush()
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
    temp_source = bin_dir / "memory_hook_runner.py"
    compiled_runner = bin_dir / "memory_hook_runner.pyc"

    if compiled_runner.exists():
        try:
            if os.name == "nt":
                subprocess.run(["attrib", "-R", str(compiled_runner)], check=False, capture_output=True)
            else:
                compiled_runner.chmod(0o755)
        except Exception:
            pass

    temp_source.write_text(runner_code, encoding="utf-8")
    import py_compile
    py_compile.compile(str(temp_source), cfile=str(compiled_runner), doraise=True)
    try: temp_source.unlink()
    except Exception: pass

    sha256 = None
    try:
        with open(compiled_runner, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
        state_data["runner_sha256"] = sha256
    except Exception:
        pass
    state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")

    try:
        if os.name == "nt":
            subprocess.run(["attrib", "+R", str(compiled_runner)], check=False, capture_output=True)
        else:
            compiled_runner.chmod(0o555)
    except Exception:
        pass

    proto_instructions = (
        "# Universal Memory Hook Protocol (.memory/)\n\n"
        "This repository is integrated with the **Cross-Provider Memory Hook**.\n"
        "All agent activities (user requests, tool calls, tool responses, model outputs) are deterministically "
        "recorded in `.memory/short.log`, maintaining a rolling context window of **100,000 tokens** (100k tokens).\n\n"
        "## Core Directives for All Agents:\n\n"
        "1. **Context Restoration at Session Start**:\n"
        "   - At the beginning of every session or task, read `.memory/long.log` first.\n"
        "   - If needed, read the tail of `.memory/short.log` (last 20-30 lines) to catch up on the most recent actions taken by any previous agent.\n"
        "   - Never ask the user to re-explain what the project is about or what has already been built.\n\n"
        "2. **Long-Term Memory Compaction**:\n"
        "   - Whenever you receive a `[MEMORY HOOK UPDATE NOTICE]` indicating ~10,000 tokens have accumulated, review the uncompacted tail of `.memory/short.log` alongside the existing `.memory/long.log`.\n"
        "   - Use your file editing tools to update `.memory/long.log`:\n"
        "     - Update header metadata: `Last Updated`, `Provider`, and `Synced Through Event: #<seq>`.\n"
        "     - Update Completed Work & Milestones with newly finished items.\n"
        "     - Update Current State & Active Focus with what you are working on right now.\n"
        "     - Update Next Immediate Steps with pending tasks.\n"
        "     - Preserve and append any newly established Architecture & Core Decisions.\n"
        "     - Keep content concise, high-density, and structured.\n\n"
        "3. **Security & Immutability Directives**:\n"
        "   - The runner at `.memory/bin/memory_hook_runner.pyc` is locally compiled bytecode locked with OS read-only permissions.\n"
        "   - Under NO circumstances should any agent attempt to edit, rewrite, replace, or delete files inside `.memory/bin/` or `.memory/state.json`.\n"
        "   - All memory updates must strictly be applied to `.memory/long.log`.\n"
    )

    def append_or_create_instructions(file_path: Path, full_content: str, append_part: str):
        if not file_path.exists():
            file_path.write_text(full_content.strip() + "\n", encoding="utf-8")
        else:
            existing = file_path.read_text(encoding="utf-8")
            if "Universal Memory Hook Protocol" not in existing:
                sep = "\n\n---\n\n" if existing.strip() else ""
                file_path.write_text(existing + sep + append_part.strip() + "\n", encoding="utf-8")

    def backup_config(f_path: Path):
        if f_path.is_file():
            shutil.copy2(f_path, f_path.with_name(f"{f_path.name}.bak"))

    def read_json_safe(f_path: Path) -> dict:
        if not f_path.is_file(): return {}
        content = f_path.read_text(encoding="utf-8").strip()
        if not content: return {}
        try:
            val = json.loads(content)
            return val if isinstance(val, dict) else {}
        except Exception:
            backup_config(f_path)
            raise RuntimeError(f"Config {f_path} is malformed JSON. Backup created.")

    root_abs = target_dir.resolve().as_posix()
    runner_abs = (bin_dir / "memory_hook_runner.pyc").resolve().as_posix()
    installed_files = {}

    targets = providers or ["all"]
    if "all" in targets:
        targets = ["antigravity", "claude", "codex", "cursor"]

    for p in targets:
        p_clean = p.lower().strip()
        if p_clean in ["antigravity", "gemini"]:
            agents_dir = target_dir / ".agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            hooks_file = agents_dir / "hooks.json"
            backup_config(hooks_file)
            existing_hooks = read_json_safe(hooks_file)
            r_arg = f'"{runner_abs}"' if " " in runner_abs else runner_abs
            rt_arg = f'"{root_abs}"' if " " in root_abs else root_abs
            existing_hooks["memory-hook"] = {
                "PreInvocation": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PreInvocation --root {rt_arg}'}],
                "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PreToolUse --root {rt_arg}'}]}],
                "PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event PostToolUse --root {rt_arg}'}]}],
                "Stop": [{"type": "command", "command": f'python {r_arg} --provider antigravity --event Stop --root {rt_arg}'}],
            }
            hooks_file.write_text(json.dumps(existing_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "AGENTS.md", f"# Antigravity Agent Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["antigravity"] = [".agents/hooks.json", "AGENTS.md"]

        elif p_clean in ["claude", "claude-code"]:
            claude_dir = target_dir / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            claude_settings = claude_dir / "settings.json"
            backup_config(claude_settings)
            existing_claude_settings = read_json_safe(claude_settings)
            hooks = existing_claude_settings.get("hooks", {})
            if not isinstance(hooks, dict): hooks = {}

            def merge_claude(event_name, cmd, matcher=""):
                lst = hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]}
                for i, existing in enumerate(lst):
                    if any("memory_hook_runner.pyc" in str(h.get("command", "")) for h in existing.get("hooks", [])):
                        lst[i] = entry
                        hooks[event_name] = lst
                        return
                lst.append(entry)
                hooks[event_name] = lst

            merge_claude("UserPromptSubmit", f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"')
            merge_claude("PreToolUse", f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"', matcher=".*")
            merge_claude("PostToolUse", f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"', matcher=".*")
            merge_claude("Stop", f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"')
            existing_claude_settings["hooks"] = hooks
            claude_settings.write_text(json.dumps(existing_claude_settings, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "CLAUDE.md", f"# Claude Code Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["claude"] = [".claude/settings.json", "CLAUDE.md"]

        elif p_clean in ["codex", "openai"]:
            codex_dir = target_dir / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            codex_hooks = codex_dir / "hooks.json"
            backup_config(codex_hooks)
            existing_codex_hooks = read_json_safe(codex_hooks)

            def merge_codex(event_name, cmd):
                lst = existing_codex_hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"type": "command", "command": cmd}
                for i, existing in enumerate(lst):
                    if "memory_hook_runner.pyc" in str(existing.get("command", "")):
                        lst[i] = entry
                        existing_codex_hooks[event_name] = lst
                        return
                lst.append(entry)
                existing_codex_hooks[event_name] = lst

            merge_codex("UserPromptSubmit", f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"')
            merge_codex("PreToolUse", f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"')
            merge_codex("PostToolUse", f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"')
            merge_codex("Stop", f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"')
            codex_hooks.write_text(json.dumps(existing_codex_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / "CODEX.md", f"# Codex Guidelines\n\n{proto_instructions}", proto_instructions)
            installed_files["codex"] = [".codex/hooks.json", "CODEX.md"]

        elif p_clean in ["cursor"]:
            cursor_dir = target_dir / ".cursor"
            cursor_dir.mkdir(parents=True, exist_ok=True)
            cursor_hooks = cursor_dir / "hooks.json"
            backup_config(cursor_hooks)
            existing_cursor_hooks = read_json_safe(cursor_hooks)

            def merge_cursor(event_name, cmd):
                lst = existing_cursor_hooks.get(event_name, [])
                if not isinstance(lst, list): lst = []
                entry = {"command": cmd}
                for i, existing in enumerate(lst):
                    if "memory_hook_runner.pyc" in str(existing.get("command", "")):
                        lst[i] = entry
                        existing_cursor_hooks[event_name] = lst
                        return
                lst.append(entry)
                existing_cursor_hooks[event_name] = lst

            merge_cursor("beforeSubmitPrompt", f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"')
            merge_cursor("preToolUse", f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"')
            merge_cursor("postToolUse", f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"')
            merge_cursor("stop", f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"')
            cursor_hooks.write_text(json.dumps(existing_cursor_hooks, indent=2), encoding="utf-8")
            append_or_create_instructions(target_dir / ".cursorrules", f"# Cursor Rules\n\n{proto_instructions}", proto_instructions)
            installed_files["cursor"] = [".cursor/hooks.json", ".cursorrules"]

    return installed_files


def main():
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Universal standalone installer for memory-hook.",
    )
    parser.add_argument(
        "target_path",
        nargs="?",
        default=None,
        help="Target directory to install memory-hook into (default: current directory)",
    )
    parser.add_argument(
        "--provider",
        choices=["all", "antigravity", "claude", "codex", "cursor"],
        default="all",
        help="Specific provider to configure (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display proposed actions without modifying files",
    )
    args = parser.parse_args()

    target_dir = Path(args.target_path).resolve() if args.target_path else Path.cwd().resolve()
    providers = [args.provider] if args.provider != "all" else ["all"]

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"{prefix}Installing universal memory-hook into: {target_dir}")
    if install_hooks is not None:
        installed = install_hooks(target_dir, providers=providers, dry_run=args.dry_run)
    else:
        installed = _standalone_install(target_dir, providers=providers, dry_run=args.dry_run)

    print(f"\n{prefix}✓ Installation {'planned' if args.dry_run else 'successful'}!")
    print("Configured providers:")
    for prov, files in installed.items():
        print(f"  • {prov}: {', '.join(files)}")

    if not args.dry_run:
        print("\nDeterministic logging is active in .memory/short.log")
        print("Agent synthesis knowledge base is in .memory/long.log")


if __name__ == "__main__":
    main()
