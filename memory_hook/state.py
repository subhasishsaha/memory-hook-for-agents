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
from .locking import safe_fsync, safe_fsync_dir


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
        "retained_events": 0,
        "lifetime_tokens_exact": True,
        "recovered_at_ts": None,
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
                self.data["retained_events"] = int(loaded["retained_events"]) if "retained_events" in loaded and loaded["retained_events"] is not None else None
                self.data["lifetime_tokens_exact"] = bool(loaded["lifetime_tokens_exact"]) if "lifetime_tokens_exact" in loaded and loaded["lifetime_tokens_exact"] is not None else True
                self.data["recovered_at_ts"] = loaded.get("recovered_at_ts")
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
            self.data["short_log_tokens"] = tokens_sum

            # If window starts at 1, short.log contains the complete project history
            self.data["retained_events"] = event_count
            self.data["total_events"] = max(self.data.get("total_events", 0), event_count)
            if min_seq is not None and min_seq <= 1:
                self.data["total_lifetime_tokens"] = tokens_sum
                self.data["lifetime_tokens_exact"] = True
            else:
                # Log was pruned in the past; lifetime tokens for pruned events cannot be recovered from disk
                self.data["total_lifetime_tokens"] = max(
                    self.data.get("total_lifetime_tokens", 0), tokens_sum
                )
                self.data["lifetime_tokens_exact"] = False

            self.data["recovered_at_ts"] = datetime.now(timezone.utc).isoformat()
            if last_ts:
                self.data["last_updated_ts"] = last_ts
            if last_provider:
                self.data["last_updated_provider"] = last_provider
            return True
        except Exception:
            return False

    def tokens_at_seq(self, target_seq: int) -> int:
        """Calculates cumulative lifetime tokens represented through a given sequence."""
        if target_seq >= self.current_seq:
            return self.total_lifetime_tokens

        # Check if there's a pending compaction recorded at this exact sequence
        pc = self.pending_compaction
        if pc and isinstance(pc, dict) and pc.get("requested_at_seq") == target_seq:
            if "lifetime_tokens_at_request" in pc:
                return int(pc["lifetime_tokens_at_request"])

        if not self.short_log_file.is_file():
            return 0

        # Sum tokens from short.log
        tokens_sum = 0
        baseline = 0
        if self.window_start_seq > 1:
            baseline = max(0, self.total_lifetime_tokens - self.short_log_tokens)

        try:
            with open(self.short_log_file, "r", encoding="utf-8", errors="ignore") as f:
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
            return self.total_lifetime_tokens

    def reconcile_with_journal(self) -> bool:
        """Reconciles sequence number and state with short.log journal.

        Treats short.log as the append-only source of truth. If a crash occurred:
        - Crash before journal append: resets current_seq to last committed journal sequence.
        - Crash after journal append: updates current_seq, total_events, and reconciles token counts.
        """
        if not self.short_log_file.is_file():
            if self.current_seq != 0:
                self.data["current_seq"] = 0
                self.save()
                return True
            return False

        last_line = ""
        tokens_sum = 0
        event_count = 0
        with open(self.short_log_file, "r", encoding="utf-8", errors="ignore") as f:
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
            if self.current_seq != 0:
                self.data["current_seq"] = 0
                self.save()
                return True
            return False

        try:
            record = json.loads(last_line)
        except Exception:
            return False
        if not isinstance(record, dict):
            return False

        journal_seq = record.get("seq")
        changed = False
        if isinstance(journal_seq, int):
            if journal_seq != self.current_seq:
                self.data["current_seq"] = journal_seq
                self.data["total_events"] = max(event_count, journal_seq)
                changed = True

        # If journal has more tokens than recorded in short_log_tokens (crash before state save)
        if tokens_sum > self.short_log_tokens:
            missing_tokens = tokens_sum - self.short_log_tokens
            self.data["short_log_tokens"] = tokens_sum
            self.data["total_lifetime_tokens"] = self.total_lifetime_tokens + missing_tokens
            changed = True

        if record.get("ts") and not self.data.get("last_updated_ts"):
            self.data["last_updated_ts"] = str(record["ts"])
            changed = True

        if record.get("provider") and not self.data.get("last_updated_provider"):
            self.data["last_updated_provider"] = str(record["provider"])
            changed = True

        if changed:
            self.save()
        return changed

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
                f.flush()
                safe_fsync(f.fileno())
            temp_file.replace(self.state_file)
            safe_fsync_dir(self.memory_dir)
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
        elif seq > self.current_seq:
            raise ValueError(
                f"Cannot request compaction for sequence #{seq} ahead of current journal sequence #{self.current_seq}"
            )
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
            "lifetime_tokens_at_request": self.tokens_at_seq(seq),
            "requested_at": now_iso,
        }
        self.data["pending_compaction"] = pending_record
        self.save()
        return pending_record

    def acknowledge_compaction(self, seq: Optional[int] = None, force: bool = False) -> bool:
        """Idempotently acknowledges long-term compaction, advancing checkpoint."""
        pc = self.pending_compaction
        has_pending = bool(pc and isinstance(pc, dict) and pc.get("status") == "pending")

        if seq is None:
            if has_pending and pc.get("requested_at_seq"):
                target_seq = int(pc["requested_at_seq"])
            else:
                target_seq = self.current_seq
        else:
            target_seq = int(seq)

        # Validation 1: Upper bound against pending request (unless forced)
        if has_pending and not force:
            requested_seq = int(pc.get("requested_at_seq", 0))
            if target_seq > requested_seq:
                raise ValueError(
                    f"Cannot acknowledge sequence #{target_seq} beyond pending compaction checkpoint #{requested_seq}. Pass force=True to override."
                )

        # Validation 2: Lower bound against pending request (unless forced)
        if has_pending and not force:
            requested_seq = int(pc.get("requested_at_seq", 0))
            if target_seq < requested_seq:
                raise ValueError(
                    f"Cannot acknowledge sequence #{target_seq} below active pending compaction checkpoint #{requested_seq}. "
                    f"Action Required: Complete synthesis through #{requested_seq} or pass force=True."
                )

        # Validation 3: cannot acknowledge sequences beyond current journal sequence
        # (pending checkpoint must NEVER validate itself ahead of current_seq)
        max_valid_seq = self.current_seq
        if target_seq > max_valid_seq:
            raise ValueError(
                f"Cannot acknowledge sequence #{target_seq} ahead of current sequence #{max_valid_seq}"
            )

        # If already at or before target_seq, this is a no-op (never discard pending compaction)
        if target_seq <= self.last_compacted_seq:
            return False

        self.data["last_compacted_seq"] = target_seq
        self.data["last_compacted_tokens"] = self.tokens_at_seq(target_seq)
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()

        # Only clear pending_compaction if this acknowledgement satisfies (>=) the requested checkpoint
        if has_pending:
            requested_seq = int(pc.get("requested_at_seq", 0))
            if target_seq >= requested_seq:
                self.data["pending_compaction"] = None
        else:
            self.data["pending_compaction"] = None

        self.save()
        return True

    def mark_compacted(self, seq: Optional[int] = None, force: bool = False) -> None:
        """Compatibility wrapper that directly acknowledges compaction."""
        self.acknowledge_compaction(seq, force=force)

    def check_long_log_sync(self, long_log_path: Path) -> bool:
        """Inspects long.log to verify if pending compaction was completed by an agent.
        
        Strictly requires the explicit markdown header `Synced Through Event: #<seq>`.
        Does not use flaky file modification time heuristics.
        """
        if not self.has_pending_compaction():
            return False

        if not long_log_path.is_file():
            return False

        synced_seq = None
        try:
            stat = long_log_path.stat()
            if stat.st_size == 0:
                return False

            with open(long_log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4096)

            match = re.search(r"Synced Through Event:\s*#(\d+)", content)
            if match:
                synced_seq = int(match.group(1))
        except (OSError, UnicodeError, ValueError):
            return False

        if synced_seq is None:
            return False

        pc = self.pending_compaction or {}
        requested_seq = int(pc.get("requested_at_seq", 0))

        # Check bounds:
        # 1. Target must be <= self.current_seq (pending checkpoint cannot validate itself)
        # 2. Cannot acknowledge sequences below requested_seq
        # 3. If requested_seq > self.current_seq, pending state is inconsistent; reject and preserve
        if requested_seq <= synced_seq <= self.current_seq:
            self.acknowledge_compaction(synced_seq, force=True)
            return True

        return False

    def commit_event(self, seq: int, tokens: int, provider: Optional[str] = None) -> None:
        """Atomically commits an event to state after it has been written and fsynced to short.log."""
        self.data["current_seq"] = seq
        self.data["total_events"] = max(int(self.data.get("total_events", 0)), seq)
        self.data["total_lifetime_tokens"] = self.total_lifetime_tokens + tokens
        self.data["short_log_tokens"] = self.short_log_tokens + tokens
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
        if provider:
            self.data["last_updated_provider"] = provider
        self.save()

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
