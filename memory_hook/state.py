"""State manager for .memory/state.json tracking sequence counters, locking, and compaction."""

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import (
    DEFAULT_COMPACT_INTERVAL,
    DEFAULT_COMPACT_TOKEN_INTERVAL,
    MAX_SHORT_LOG_TOKENS,
    SHORT_LOG_NAME,
    STATE_FILE_NAME,
)


class MemoryState:
    """Manages persistent checkpoint metadata for the memory hook."""

    DEFAULT_DATA: Dict[str, Any] = {
        "version": 2,
        "current_seq": 0,
        "last_compacted_seq": 0,
        "last_updated_ts": None,
        "last_updated_provider": None,
        "total_events": 0,
        "total_lifetime_tokens": 0,
        "last_compacted_tokens": 0,
        "short_log_tokens": 0,
        "window_start_seq": 1,
        "pending_compaction": None,
        "runner_sha256": None,
    }

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / STATE_FILE_NAME
        self.short_log_file = self.memory_dir / SHORT_LOG_NAME
        self.data: Dict[str, Any] = dict(self.DEFAULT_DATA)
        self.load()

    def load(self) -> None:
        """Loads state from file if it exists, validates, and recovers if corrupted."""
        if not self.state_file.is_file():
            return

        is_corrupt = False
        loaded = None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    is_corrupt = True
                else:
                    loaded = json.loads(content)
                    if not isinstance(loaded, dict):
                        is_corrupt = True
        except Exception:
            is_corrupt = True

        if is_corrupt:
            self._handle_corrupt_state()
            return

        if isinstance(loaded, dict):
            # Validate basic types
            try:
                self.data["current_seq"] = int(loaded.get("current_seq", 0))
                self.data["last_compacted_seq"] = int(loaded.get("last_compacted_seq", 0))
                self.data["total_events"] = int(loaded.get("total_events", 0))
                self.data["total_lifetime_tokens"] = int(loaded.get("total_lifetime_tokens", 0))
                self.data["last_compacted_tokens"] = int(loaded.get("last_compacted_tokens", 0))
                self.data["short_log_tokens"] = int(loaded.get("short_log_tokens", 0))
                self.data["window_start_seq"] = int(loaded.get("window_start_seq", 1))
                self.data["last_updated_ts"] = loaded.get("last_updated_ts")
                self.data["last_updated_provider"] = loaded.get("last_updated_provider")
                self.data["pending_compaction"] = loaded.get("pending_compaction")
                self.data["runner_sha256"] = loaded.get("runner_sha256")
                self.data["version"] = int(loaded.get("version", 2))
            except (ValueError, TypeError):
                self._handle_corrupt_state()

    def _handle_corrupt_state(self) -> None:
        """Backs up corrupt state and reconstructs sequence metrics from short.log."""
        now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{STATE_FILE_NAME}.corrupt.{now_ts}"
        backup_path = self.memory_dir / backup_name

        try:
            if self.state_file.is_file():
                shutil.copy2(self.state_file, backup_path)
        except Exception:
            pass

        self.recover_from_short_log()
        self.save()

    def recover_from_short_log(self) -> bool:
        """Scans short.log to reconstruct valid sequence numbers and token counts."""
        if not self.short_log_file.is_file():
            return False

        max_seq = 0
        min_seq = None
        event_count = 0
        tokens_sum = 0
        last_ts = None
        last_provider = None

        try:
            with open(self.short_log_file, "r", encoding="utf-8", errors="ignore") as f:
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

            self.data["current_seq"] = max_seq
            self.data["window_start_seq"] = min_seq if min_seq is not None else 1
            self.data["total_events"] = max(self.data.get("total_events", 0), event_count)
            self.data["short_log_tokens"] = tokens_sum
            self.data["total_lifetime_tokens"] = max(
                self.data.get("total_lifetime_tokens", 0), tokens_sum
            )
            if last_ts:
                self.data["last_updated_ts"] = last_ts
            if last_provider:
                self.data["last_updated_provider"] = last_provider
            return True
        except Exception:
            return False

    def save(self) -> None:
        """Atomically saves state to disk."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.memory_dir),
                delete=False,
            ) as f:
                temp_file = Path(f.name)
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
        except Exception:
            try:
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    @property
    def current_seq(self) -> int:
        return int(self.data.get("current_seq", 0))

    @property
    def total_events(self) -> int:
        return int(self.data.get("total_events", 0))

    @property
    def last_compacted_seq(self) -> int:
        return int(self.data.get("last_compacted_seq", 0))

    @property
    def total_lifetime_tokens(self) -> int:
        return int(self.data.get("total_lifetime_tokens", 0))

    @property
    def last_compacted_tokens(self) -> int:
        return int(self.data.get("last_compacted_tokens", 0))

    @property
    def short_log_tokens(self) -> int:
        return int(self.data.get("short_log_tokens", 0))

    @property
    def window_start_seq(self) -> int:
        return int(self.data.get("window_start_seq", 1))

    @property
    def uncompacted_tokens(self) -> int:
        return max(0, self.total_lifetime_tokens - self.last_compacted_tokens)

    @property
    def pending_compaction(self) -> Optional[Dict[str, Any]]:
        return self.data.get("pending_compaction")

    def has_pending_compaction(self) -> bool:
        """Returns True if a compaction request is active and awaiting acknowledgement."""
        pc = self.pending_compaction
        return bool(pc and isinstance(pc, dict) and pc.get("status") == "pending")

    def request_compaction(
        self, seq: Optional[int] = None, uncompacted_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """Creates a pending compaction request if none is already pending."""
        if seq is None:
            seq = self.current_seq
        if uncompacted_tokens is None:
            uncompacted_tokens = self.uncompacted_tokens

        existing = self.data.get("pending_compaction")
        if existing and isinstance(existing, dict) and existing.get("status") == "pending":
            return existing

        now_iso = datetime.now(timezone.utc).isoformat()
        pending_record: Dict[str, Any] = {
            "status": "pending",
            "requested_at_seq": seq,
            "requested_tokens": uncompacted_tokens,
            "requested_at": now_iso,
        }
        self.data["pending_compaction"] = pending_record
        self.save()
        return pending_record

    def acknowledge_compaction(self, seq: Optional[int] = None) -> bool:
        """Idempotently acknowledges long-term compaction, advancing checkpoint."""
        target_seq = seq
        if target_seq is None:
            pc = self.pending_compaction
            if pc and isinstance(pc, dict) and pc.get("requested_at_seq"):
                target_seq = int(pc["requested_at_seq"])
            else:
                target_seq = self.current_seq

        # If already at or beyond target_seq and no compaction is pending, no-op
        if target_seq <= self.last_compacted_seq and not self.has_pending_compaction():
            return False

        if target_seq < self.last_compacted_seq:
            self.data["pending_compaction"] = None
            self.save()
            return False

        self.data["last_compacted_seq"] = target_seq
        self.data["last_compacted_tokens"] = self.total_lifetime_tokens
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
        self.data["pending_compaction"] = None
        self.save()
        return True

    def mark_compacted(self, seq: Optional[int] = None) -> None:
        """Compatibility wrapper that directly acknowledges compaction."""
        self.acknowledge_compaction(seq)

    def check_long_log_sync(self, long_log_path: Path) -> bool:
        """Inspects long.log to verify if pending compaction was completed by an agent."""
        if not self.has_pending_compaction():
            return False

        if not long_log_path.is_file():
            return False

        try:
            stat = long_log_path.stat()
            if stat.st_size == 0:
                return False

            with open(long_log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4096)

            match = re.search(r"Synced Through Event:\s*#(\d+)", content)
            pc = self.pending_compaction or {}
            requested_seq = int(pc.get("requested_at_seq", 0))

            if match:
                synced_seq = int(match.group(1))
                if synced_seq >= requested_seq:
                    self.acknowledge_compaction(synced_seq)
                    return True

            # Alternatively, check modification time against request timestamp
            requested_at_str = pc.get("requested_at")
            if requested_at_str:
                req_dt = datetime.fromisoformat(requested_at_str)
                mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if mtime_dt > req_dt and stat.st_size > 100:
                    self.acknowledge_compaction(requested_seq)
                    return True
        except Exception:
            pass

        return False

    def add_tokens(self, tokens: int) -> None:
        """Adds tokens to total lifetime and short.log token counters."""
        self.data["total_lifetime_tokens"] = self.total_lifetime_tokens + tokens
        self.data["short_log_tokens"] = self.short_log_tokens + tokens
        self.save()

    def next_seq(self, provider: Optional[str] = None) -> int:
        """Increments and returns the next sequence number."""
        self.data["current_seq"] = self.current_seq + 1
        self.data["total_events"] = int(self.data.get("total_events", 0)) + 1
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
        if provider:
            self.data["last_updated_provider"] = provider
        self.save()
        return self.current_seq

    def is_compaction_due(
        self,
        token_interval: int = DEFAULT_COMPACT_TOKEN_INTERVAL,
        event_interval: Optional[int] = None,
    ) -> bool:
        """Checks if enough tokens (default 10k) have accumulated since last compaction."""
        if token_interval <= 100:
            return (self.current_seq - self.last_compacted_seq) >= token_interval

        token_due = self.uncompacted_tokens >= token_interval
        event_due = False
        if event_interval is not None:
            event_due = (self.current_seq - self.last_compacted_seq) >= event_interval
        return token_due or event_due
