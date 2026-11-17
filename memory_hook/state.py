"""State manager for .memory/state.json tracking sequence counters and compaction."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import DEFAULT_COMPACT_INTERVAL, STATE_FILE_NAME


class MemoryState:
    """Manages persistent checkpoint metadata for the memory hook."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / STATE_FILE_NAME
        self.data: Dict[str, Any] = {
            "version": 1,
            "current_seq": 0,
            "last_compacted_seq": 0,
            "last_updated_ts": None,
            "last_updated_provider": None,
            "total_events": 0,
        }
        self.load()

    def load(self) -> None:
        """Loads state from file if it exists, otherwise retains default state."""
        if self.state_file.is_file():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
            except Exception:
                # If reading fails or file is corrupt, retain in-memory state
                pass

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
            # Fallback direct write if atomic replacement fails
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
    def last_compacted_seq(self) -> int:
        return int(self.data.get("last_compacted_seq", 0))

    def next_seq(self, provider: Optional[str] = None) -> int:
        """Increments and returns the next sequence number."""
        self.data["current_seq"] = self.current_seq + 1
        self.data["total_events"] = int(self.data.get("total_events", 0)) + 1
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
        if provider:
            self.data["last_updated_provider"] = provider
        self.save()
        return self.current_seq

    def is_compaction_due(self, interval: int = DEFAULT_COMPACT_INTERVAL) -> bool:
        """Checks if enough messages/events have accumulated since last compaction."""
        return (self.current_seq - self.last_compacted_seq) >= interval

    def mark_compacted(self, seq: Optional[int] = None) -> None:
        """Marks the current or specified sequence number as compacted."""
        if seq is None:
            seq = self.current_seq
        self.data["last_compacted_seq"] = seq
        self.data["last_updated_ts"] = datetime.now(timezone.utc).isoformat()
        self.save()
