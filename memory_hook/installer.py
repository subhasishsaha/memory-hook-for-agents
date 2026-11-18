"""Installer module to configure hooks and instructions across providers non-destructively."""

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def initialize_memory_directory(project_root: Path, dry_run: bool = False) -> Path:
    """Creates .memory directory, initializes short.log, long.log, state.json, and bin/."""
    memory_dir = project_root / MEMORY_DIR_NAME
    bin_dir = memory_dir / BIN_DIR_NAME
    if dry_run:
        return memory_dir

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

    # 4. Deploy self-contained portable runner script to .memory/bin/
    legacy_source = bin_dir / RUNNER_SOURCE_NAME
    if legacy_source.is_file():
        try:
            legacy_source.unlink()
        except Exception:
            pass

    runner_target = bin_dir / RUNNER_SCRIPT_NAME
    sha256 = generate_standalone_runner(runner_target)
    if sha256:
        state.data["runner_sha256"] = sha256
    state.save()

    return memory_dir


def verify_runner_integrity(memory_dir: Path) -> Tuple[bool, str]:
    """Verifies that installed runner matches recorded SHA-256 hash."""
    state = MemoryState(memory_dir)
    expected_hash = state.data.get("runner_sha256")
    runner_path = memory_dir / BIN_DIR_NAME / RUNNER_COMPILED_NAME
    if not runner_path.is_file():
        return False, "Runner binary missing"
    if not expected_hash:
        return True, "No integrity baseline recorded"

    try:
        with open(runner_path, "rb") as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()
        if current_hash == expected_hash:
            return True, "Integrity verified (SHA-256 match)"
        return False, f"Integrity mismatch (expected {expected_hash[:8]}..., found {current_hash[:8]}...)"
    except Exception as e:
        return False, f"Integrity check error: {e}"


def generate_standalone_runner(target_path: Path) -> Optional[str]:
    """Generates a zero-dependency, self-contained Python runner in .memory/bin/ and returns SHA-256."""
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
    bin_dir = target_path.parent
    compiled_path = bin_dir / RUNNER_COMPILED_NAME
    temp_source_path = bin_dir / RUNNER_SOURCE_NAME

    if compiled_path.exists():
        try:
            if os.name == "nt":
                import subprocess
                subprocess.run(["attrib", "-R", str(compiled_path)], check=False, capture_output=True)
            else:
                compiled_path.chmod(0o755)
        except Exception:
            pass

    with open(temp_source_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    import py_compile
    py_compile.compile(str(temp_source_path), cfile=str(compiled_path), doraise=True)

    try:
        temp_source_path.unlink()
    except Exception:
        pass

    sha256 = None
    try:
        with open(compiled_path, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        pass

    try:
        if os.name == "nt":
            import subprocess
            subprocess.run(["attrib", "+R", str(compiled_path)], check=False, capture_output=True)
        else:
            compiled_path.chmod(0o555)
    except Exception:
        pass

    return sha256


def safe_backup_file(file_path: Path) -> Optional[Path]:
    """Creates a timestamped backup before modifying a configuration file."""
    if not file_path.is_file():
        return None
    backup_path = file_path.with_name(f"{file_path.name}.bak")
    try:
        shutil.copy2(file_path, backup_path)
        return backup_path
    except Exception:
        return None


def read_json_safely(file_path: Path) -> Dict[str, Any]:
    """Reads a JSON file safely. If corrupt, creates a corrupt backup and raises RuntimeError."""
    if not file_path.is_file():
        return {}
    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    try:
        loaded = json.loads(content)
        if isinstance(loaded, dict):
            return loaded
        raise ValueError("JSON root must be an object")
    except Exception as e:
        now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        corrupt_backup = file_path.with_name(f"{file_path.name}.corrupt.{now_ts}")
        try:
            shutil.copy2(file_path, corrupt_backup)
        except Exception:
            pass
        raise RuntimeError(
            f"Existing config {file_path} is malformed JSON ({e}). "
            f"Preserved damaged backup to {corrupt_backup.name}."
        )


def append_or_create_instructions(
    file_path: Path,
    full_new_content: str,
    append_protocol: str = AGENT_MEMORY_INSTRUCTIONS,
    dry_run: bool = False,
) -> None:
    """Creates instruction file or appends memory protocol at the bottom as separate section."""
    if dry_run:
        return

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


def install_antigravity(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Antigravity .agents/hooks.json and AGENTS.md non-destructively."""
    agents_dir = project_root / ".agents"
    hooks_file = agents_dir / "hooks.json"
    agents_md = project_root / "AGENTS.md"

    if dry_run:
        return [hooks_file, agents_md]

    agents_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()
    r_arg = quote_if_needed(runner_abs)
    rt_arg = quote_if_needed(root_abs)

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

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

    # Non-destructive: merge into existing hooks
    existing_hooks["memory-hook"] = memory_hook_config
    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(agents_md, get_agents_md_content())
    return [hooks_file, agents_md]


def install_claude(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Claude Code .claude/settings.json and CLAUDE.md non-destructively."""
    claude_dir = project_root / ".claude"
    settings_file = claude_dir / "settings.json"
    claude_md = project_root / "CLAUDE.md"

    if dry_run:
        return [settings_file, claude_md]

    claude_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(settings_file)
    existing_settings = read_json_safely(settings_file)

    hooks = existing_settings.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    def merge_hook_list(event_name: str, cmd_str: str, matcher: str = "") -> None:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        # Check if memory-hook entry already exists
        found = False
        new_entry = {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": cmd_str}]
        }
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and "hooks" in entry:
                for sub_hook in entry.get("hooks", []):
                    if "memory_hook_runner.pyc" in str(sub_hook.get("command", "")) or "memory-hook" in str(sub_hook.get("command", "")):
                        entries[idx] = new_entry
                        found = True
                        break
            if found:
                break
        if not found:
            entries.append(new_entry)
        hooks[event_name] = entries

    merge_hook_list(
        "UserPromptSubmit",
        f'python "{runner_abs}" --provider claude-code --event UserPromptSubmit --root "{root_abs}"',
        matcher=""
    )
    merge_hook_list(
        "PreToolUse",
        f'python "{runner_abs}" --provider claude-code --event PreToolUse --root "{root_abs}"',
        matcher=".*"
    )
    merge_hook_list(
        "PostToolUse",
        f'python "{runner_abs}" --provider claude-code --event PostToolUse --root "{root_abs}"',
        matcher=".*"
    )
    merge_hook_list(
        "Stop",
        f'python "{runner_abs}" --provider claude-code --event Stop --root "{root_abs}"',
        matcher=""
    )

    existing_settings["hooks"] = hooks
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(existing_settings, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(claude_md, get_claude_md_content())
    return [settings_file, claude_md]


def install_codex(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures OpenAI Codex .codex/hooks.json and CODEX.md non-destructively."""
    codex_dir = project_root / ".codex"
    hooks_file = codex_dir / "hooks.json"
    codex_md = project_root / "CODEX.md"

    if dry_run:
        return [hooks_file, codex_md]

    codex_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

    def merge_simple_hook(event_name: str, cmd_str: str) -> None:
        entries = existing_hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        found = False
        new_entry = {"type": "command", "command": cmd_str}
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and ("memory_hook_runner.pyc" in str(entry.get("command", "")) or "memory-hook" in str(entry.get("command", ""))):
                entries[idx] = new_entry
                found = True
                break
        if not found:
            entries.append(new_entry)
        existing_hooks[event_name] = entries

    merge_simple_hook("UserPromptSubmit", f'python "{runner_abs}" --provider codex --event UserPromptSubmit --root "{root_abs}"')
    merge_simple_hook("PreToolUse", f'python "{runner_abs}" --provider codex --event PreToolUse --root "{root_abs}"')
    merge_simple_hook("PostToolUse", f'python "{runner_abs}" --provider codex --event PostToolUse --root "{root_abs}"')
    merge_simple_hook("Stop", f'python "{runner_abs}" --provider codex --event Stop --root "{root_abs}"')

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(codex_md, get_codex_md_content())
    return [hooks_file, codex_md]


def install_cursor(project_root: Path, dry_run: bool = False) -> List[Path]:
    """Configures Cursor .cursor/hooks.json and .cursorrules non-destructively."""
    cursor_dir = project_root / ".cursor"
    hooks_file = cursor_dir / "hooks.json"
    cursorrules = project_root / ".cursorrules"

    if dry_run:
        return [hooks_file, cursorrules]

    cursor_dir.mkdir(parents=True, exist_ok=True)
    root_abs = project_root.resolve().as_posix()
    runner_abs = (project_root / ".memory" / "bin" / RUNNER_SCRIPT_NAME).resolve().as_posix()

    safe_backup_file(hooks_file)
    existing_hooks = read_json_safely(hooks_file)

    def merge_cursor_hook(event_name: str, cmd_str: str) -> None:
        entries = existing_hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        found = False
        new_entry = {"command": cmd_str}
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict) and ("memory_hook_runner.pyc" in str(entry.get("command", "")) or "memory-hook" in str(entry.get("command", ""))):
                entries[idx] = new_entry
                found = True
                break
        if not found:
            entries.append(new_entry)
        existing_hooks[event_name] = entries

    merge_cursor_hook("beforeSubmitPrompt", f'python "{runner_abs}" --provider cursor --event beforeSubmitPrompt --root "{root_abs}"')
    merge_cursor_hook("preToolUse", f'python "{runner_abs}" --provider cursor --event preToolUse --root "{root_abs}"')
    merge_cursor_hook("postToolUse", f'python "{runner_abs}" --provider cursor --event postToolUse --root "{root_abs}"')
    merge_cursor_hook("stop", f'python "{runner_abs}" --provider cursor --event stop --root "{root_abs}"')

    with open(hooks_file, "w", encoding="utf-8") as f:
        json.dump(existing_hooks, f, indent=2, ensure_ascii=False)

    append_or_create_instructions(cursorrules, get_cursorrules_content())
    return [hooks_file, cursorrules]


def install_hooks(
    project_root: Optional[Path] = None,
    providers: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, List[str]]:
    """Installs memory-hook into the designated project repository non-destructively."""
    root = (project_root or Path.cwd()).resolve()
    initialize_memory_directory(root, dry_run=dry_run)

    installed_files: Dict[str, List[str]] = {}
    targets = providers or ["all"]
    if "all" in targets:
        targets = [PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_CURSOR]

    for p in targets:
        p_clean = p.lower().strip()
        if p_clean in [PROVIDER_ANTIGRAVITY, "gemini"]:
            files = install_antigravity(root, dry_run=dry_run)
            installed_files[PROVIDER_ANTIGRAVITY] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CLAUDE, "claude"]:
            files = install_claude(root, dry_run=dry_run)
            installed_files[PROVIDER_CLAUDE] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CODEX, "openai"]:
            files = install_codex(root, dry_run=dry_run)
            installed_files[PROVIDER_CODEX] = [str(f.relative_to(root)) for f in files]
        elif p_clean in [PROVIDER_CURSOR, "cursor"]:
            files = install_cursor(root, dry_run=dry_run)
            installed_files[PROVIDER_CURSOR] = [str(f.relative_to(root)) for f in files]

    return installed_files
