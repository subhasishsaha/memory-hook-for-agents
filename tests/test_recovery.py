"""Unit tests for state recovery from journal and corruption handling."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import SHORT_LOG_NAME, STATE_FILE_NAME
from memory_hook.state import MemoryState


class TestStateRecovery(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mem_dir = Path(self.test_dir) / ".memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_reconstruct_from_short_log(self):
        short_log = self.mem_dir / SHORT_LOG_NAME
        lines = [
            json.dumps({"seq": 10, "tokens": 50, "ts": "2026-11-18T01:00:00Z", "provider": "antigravity"}) + "\n",
            json.dumps({"seq": 11, "tokens": 75, "ts": "2026-11-18T01:05:00Z", "provider": "claude-code"}) + "\n",
            json.dumps({"seq": 12, "tokens": 120, "ts": "2026-11-18T01:10:00Z", "provider": "codex"}) + "\n",
        ]
        short_log.write_text("".join(lines), encoding="utf-8")

        # Create a corrupt state.json
        state_file = self.mem_dir / STATE_FILE_NAME
        state_file.write_text("{ broken corrupted json content !!!", encoding="utf-8")

        # Initializing MemoryState should detect corruption, create backup, and recover from short.log
        state = MemoryState(self.mem_dir)

        # Check backup was created
        backups = list(self.mem_dir.glob("state.json.corrupt.*"))
        self.assertTrue(len(backups) > 0)

        # Verify reconstructed counters
        self.assertEqual(state.current_seq, 12)
        self.assertEqual(state.window_start_seq, 10)
        self.assertEqual(state.total_events, 3)
        self.assertEqual(state.short_log_tokens, 245)
        self.assertEqual(state.total_lifetime_tokens, 245)
        self.assertEqual(state.data.get("last_updated_provider"), "codex")
        self.assertEqual(state.data.get("last_updated_ts"), "2026-11-18T01:10:00Z")


if __name__ == "__main__":
    unittest.main()
