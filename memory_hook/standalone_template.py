"""Canonical source template for the zero-dependency standalone runner (.memory/bin/memory_hook_runner.pyc)."""

STANDALONE_RUNNER_CODE = r'''#!/usr/bin/env python3
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
    (re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"), "[REDACTED:API_KEY]"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b"), "[REDACTED:GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{20,}\b"), "[REDACTED:GITHUB_PAT]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"), "[REDACTED:GOOGLE_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:AWS_KEY_ID]"),
    (re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,}\b"), "[REDACTED:SLACK_TOKEN]"),
    (re.compile(r"(?i)\bBearer\s+[a-zA-Z0-9_\-\.]{20,}\b"), "Bearer [REDACTED:BEARER_TOKEN]"),
    (re.compile(r"""(?i)\b([a-zA-Z0-9_]*(?:api[_-]?key|secret|password|passwd|auth[_-]?token|access[_-]?token))\s*[:=]\s*["']?([^\s"']{8,})["']?"""), r"\1=[REDACTED:SECRET]"),
]

SENSITIVE_FILE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.staging", ".env.development",
    "id_rsa", "id_ecdsa", "id_ed25519",
}
SENSITIVE_EXTENSIONS = {".pem", ".key", ".pkcs12", ".pfx", ".p12"}

def is_sensitive_path(path_str):
    try:
        p = Path(path_str.strip().strip("'\""))
        nl = p.name.lower()
        if nl in SENSITIVE_FILE_NAMES or nl.startswith(".env") or nl.endswith(".env") or ".env." in nl:
            return True
        if p.suffix.lower() in SENSITIVE_EXTENSIONS:
            return True
        if ("credential" in nl or "secret" in nl) and p.suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".txt"}:
            return True
    except Exception: pass
    return False

def redact_val(val):
    if isinstance(val, str):
        res = val
        for pat, rep in REDACTION_PATTERNS:
            res = pat.sub(rep, res)
        return res
    elif isinstance(val, dict):
        new_d = {}
        # Check if dict represents reading a sensitive file
        path_arg = val.get("path") or val.get("Path") or val.get("file_path") or val.get("filepath")
        if isinstance(path_arg, str) and is_sensitive_path(path_arg):
            for k, v in val.items():
                if k in ("content", "Content", "output", "result", "replacement", "Replacement"):
                    new_d[k] = "[REDACTED:SENSITIVE_FILE_CONTENT]"
                else:
                    new_d[k] = redact_val(v)
            return new_d

        for k, v in val.items():
            if any(s in str(k).lower() for s in ["password", "secret", "api_key", "token", "private_key"]) and isinstance(v, str):
                new_d[k] = "[REDACTED:SECRET]"
            else:
                new_d[k] = redact_val(v)
        return new_d
    elif isinstance(val, list):
        return [redact_val(x) for x in val]
    return val

class LockTimeoutError(Exception):
    """Raised when file lock cannot be acquired within timeout."""
    pass

class StandaloneLock:
    def __init__(self, lock_path, timeout=5.0, poll_interval=0.02):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.poll_interval = poll_interval
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
                elapsed = time.monotonic() - start
                if elapsed >= self.timeout:
                    raise LockTimeoutError(f"Timed out after {self.timeout}s waiting for lock: {self.lock_path}")
                time.sleep(self.poll_interval)
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

def recover_from_short_log(mem_dir, state):
    sl = mem_dir / SHORT_LOG_NAME
    if not sl.is_file():
        return False
    max_seq = 0
    min_seq = None
    event_count = 0
    tokens_sum = 0
    last_ts = None
    last_provider = None
    try:
        with open(sl, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_clean = line.strip()
                if not line_clean:
                    continue
                try:
                    record = json.loads(line_clean)
                    if not isinstance(record, dict):
                        continue
                    seq = record.get("seq")
                    if isinstance(seq, int):
                        if seq > max_seq:
                            max_seq = seq
                        if min_seq is None or seq < min_seq:
                            min_seq = seq
                    event_count += 1
                    tok = record.get("tokens", 0)
                    if isinstance(tok, int):
                        tokens_sum += tok
                    if record.get("ts"):
                        last_ts = str(record["ts"])
                    if record.get("provider"):
                        last_provider = str(record["provider"])
                except Exception:
                    continue
        state["current_seq"] = max_seq
        state["window_start_seq"] = min_seq if min_seq is not None else 1
        state["short_log_tokens"] = tokens_sum
        state["retained_events"] = event_count
        state["total_events"] = max(state.get("total_events", 0), event_count)
        if min_seq is not None and min_seq <= 1:
            state["total_lifetime_tokens"] = tokens_sum
            state["lifetime_tokens_exact"] = True
        else:
            state["total_lifetime_tokens"] = max(state.get("total_lifetime_tokens", 0), tokens_sum)
            state["lifetime_tokens_exact"] = False
        state["recovered_at_ts"] = datetime.now(timezone.utc).isoformat()
        if last_ts:
            state["last_updated_ts"] = last_ts
        if last_provider:
            state["last_updated_provider"] = last_provider
        return True
    except Exception:
        return False

def handle_corrupt_state(mem_dir, default_state):
    sf = mem_dir / STATE_FILE_NAME
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = mem_dir / f"{STATE_FILE_NAME}.corrupt.{now_ts}"
    try:
        if sf.is_file():
            import shutil
            shutil.copy2(sf, backup_path)
    except Exception:
        pass
    recover_from_short_log(mem_dir, default_state)
    save_state(mem_dir, default_state)
    return default_state

def load_state(mem_dir):
    sf = mem_dir / STATE_FILE_NAME
    default_state = {
        "version": 2, "current_seq": 0, "last_compacted_seq": 0,
        "total_events": 0, "total_lifetime_tokens": 0, "last_compacted_tokens": 0,
        "short_log_tokens": 0, "window_start_seq": 1, "pending_compaction": None
    }
    if sf.is_file():
        is_corrupt = False
        try:
            with open(sf, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    is_corrupt = True
                else:
                    d = json.load(f)
                    if isinstance(d, dict):
                        default_state.update(d)
                        return default_state
                    else:
                        is_corrupt = True
        except Exception:
            is_corrupt = True
        if is_corrupt:
            return handle_corrupt_state(mem_dir, default_state)
    return default_state

def save_state(mem_dir, state):
    sf = mem_dir / STATE_FILE_NAME
    tf = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(mem_dir), delete=False) as f:
            tf = Path(f.name)
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        tf.replace(sf)
    finally:
        if tf and tf.exists():
            try: tf.unlink()
            except Exception: pass

def tokens_at_seq(mem_dir, state, target_seq):
    if target_seq >= state.get("current_seq", 0):
        return state.get("total_lifetime_tokens", 0)
    pc = state.get("pending_compaction")
    if pc and isinstance(pc, dict) and pc.get("requested_at_seq") == target_seq:
        if "lifetime_tokens_at_request" in pc:
            return int(pc["lifetime_tokens_at_request"])
    sl = mem_dir / SHORT_LOG_NAME
    if not sl.is_file():
        return 0
    tokens_sum = 0
    baseline = 0
    if state.get("window_start_seq", 1) > 1:
        baseline = max(0, state.get("total_lifetime_tokens", 0) - state.get("short_log_tokens", 0))
    try:
        with open(sl, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_clean = line.strip()
                if not line_clean:
                    continue
                try:
                    record = json.loads(line_clean)
                    r_seq = record.get("seq")
                    if isinstance(r_seq, int) and r_seq <= target_seq:
                        tok = record.get("tokens", 0)
                        if isinstance(tok, int):
                            tokens_sum += tok
                except Exception:
                    continue
        return baseline + tokens_sum
    except Exception:
        return state.get("total_lifetime_tokens", 0)

def reconcile_with_journal(mem_dir, state):
    sl = mem_dir / SHORT_LOG_NAME
    if not sl.is_file():
        if state.get("current_seq", 0) != 0:
            state["current_seq"] = 0
            save_state(mem_dir, state)
            return True
        return False
    try:
        last_line = ""
        tokens_sum = 0
        event_count = 0
        with open(sl, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_clean = line.strip()
                if line_clean:
                    last_line = line_clean
                    event_count += 1
                    try:
                        rec = json.loads(line_clean)
                        if isinstance(rec, dict):
                            tok = rec.get("tokens", 0)
                            if isinstance(tok, int):
                                tokens_sum += tok
                    except Exception:
                        pass
        if not last_line:
            if state.get("current_seq", 0) != 0:
                state["current_seq"] = 0
                save_state(mem_dir, state)
                return True
            return False
        record = json.loads(last_line)
        if not isinstance(record, dict):
            return False
        journal_seq = record.get("seq")
        changed = False
        if isinstance(journal_seq, int) and journal_seq != state.get("current_seq", 0):
            state["current_seq"] = journal_seq
            state["total_events"] = max(event_count, journal_seq)
            changed = True
        if tokens_sum > state.get("short_log_tokens", 0):
            missing = tokens_sum - state.get("short_log_tokens", 0)
            state["short_log_tokens"] = tokens_sum
            state["total_lifetime_tokens"] = state.get("total_lifetime_tokens", 0) + missing
            changed = True
        if record.get("ts") and not state.get("last_updated_ts"):
            state["last_updated_ts"] = str(record["ts"])
            changed = True
        if record.get("provider") and not state.get("last_updated_provider"):
            state["last_updated_provider"] = str(record["provider"])
            changed = True
        if changed:
            save_state(mem_dir, state)
        return changed
    except Exception:
        return False

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
        match = re.search(r"Synced Through Event:\s*#(\d+)", content)
        requested_seq = int(pc.get("requested_at_seq", 0))
        if match:
            synced_seq = int(match.group(1))
            if synced_seq >= requested_seq:
                target_seq = min(synced_seq, state.get("current_seq", 0))
                state["last_compacted_seq"] = target_seq
                state["last_compacted_tokens"] = tokens_at_seq(mem_dir, state, target_seq)
                state["pending_compaction"] = None
                state["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
                save_state(mem_dir, state)
                return True
    except Exception: pass
    return False

def truncate_string(val, max_len=MAX_PAYLOAD_STRING_LENGTH):
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + f"\n... [truncated {len(val)-max_len} characters]", True
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
    last_compacted = state.get("last_compacted_seq", 0)

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

        is_uncompacted = (rec_seq is not None and rec_seq > last_compacted)
        if not is_uncompacted and tokens + lt > MAX_SHORT_LOG_TOKENS and kept:
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
                    f.write(l if l.endswith("\n") else l + "\n")
                f.flush()
                try: os.fsync(f.fileno())
                except Exception: pass
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
            reconcile_with_journal(mem_dir, state)
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
                call_id = raw_payload.get("id") or raw_payload.get("tool_use_id") or f"call_{seq}"
                tool_name = raw_payload.get("tool_name") or raw_payload.get("name") or "tool"
                payload = {"call_id": call_id, "tool_name": tool_name, "args": tc}
            elif "posttool" in evt:
                event_type = "tool_result"
                call_id = raw_payload.get("id") or raw_payload.get("tool_use_id") or f"call_{seq}"
                tool_name = raw_payload.get("tool_name") or raw_payload.get("name") or "tool"
                payload = {
                    "call_id": call_id,
                    "tool_name": tool_name,
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

            # Write-ahead: append to journal first
            with open(mem_dir / SHORT_LOG_NAME, "a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                try: os.fsync(f.fileno())
                except Exception: pass

            # Update token accounting and prune rolling context
            state["total_lifetime_tokens"] = state.get("total_lifetime_tokens", 0) + event_tokens
            state["short_log_tokens"] = state.get("short_log_tokens", 0) + event_tokens
            prune_short_log(mem_dir, state)

            uncompacted = state["total_lifetime_tokens"] - state.get("last_compacted_tokens", 0)
            if uncompacted >= DEFAULT_COMPACT_TOKEN_INTERVAL and not state.get("pending_compaction"):
                state["pending_compaction"] = {
                    "status": "pending",
                    "requested_at_seq": seq,
                    "requested_tokens": uncompacted,
                    "lifetime_tokens_at_request": tokens_at_seq(mem_dir, state, seq),
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
                    sys.stderr.write(f"\n{notice}\n")
                    sys.stderr.flush()
                resp = {"status": "ok"}

            save_state(mem_dir, state)

        sys.stdout.write(json.dumps(resp))
        sys.stdout.flush()
    except LockTimeoutError as lte:
        sys.stderr.write(f"memory-hook runner: {lte}\n")
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"status": "ok", "decision": "allow"}))
        sys.stdout.flush()
        sys.exit(0)
    except Exception as exc:
        sys.stderr.write(f"memory-hook runner warning: {exc}\n")
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"status": "ok", "decision": "allow"}))
        sys.stdout.flush()
        sys.exit(0)

if __name__ == "__main__":
    main()
'''
