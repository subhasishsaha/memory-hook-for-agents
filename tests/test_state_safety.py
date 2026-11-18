"""Unit tests for state write safety, crash reconciliation, and acknowledgement validation."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_hook.constants import SHORT_LOG_NAME, STATE_FILE_NAME
from memory_hook.state import MemoryState


class TestStateSafety(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)
        self.mem_dir = self.root / ".memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self.short_log = self.mem_dir / SHORT_LOG_NAME

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_journal_reconciliation_after_simulated_crash(self):
        """Reconciliation aligns state.current_seq with the last committed record in short.log."""
        # 1. State recorded current_seq = 5
        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 5
        state.data["total_events"] = 5
        state.save()

        # 2. Simulate short.log journal having advanced to seq #8 before crash
        records = [
            {"seq": 5, "ts": "2026-11-18T00:05:00Z", "provider": "antigravity", "tokens": 10},
            {"seq": 6, "ts": "2026-11-18T00:06:00Z", "provider": "antigravity", "tokens": 12},
            {"seq": 7, "ts": "2026-11-18T00:07:00Z", "provider": "antigravity", "tokens": 14},
            {"seq": 8, "ts": "2026-11-18T00:08:00Z", "provider": "antigravity", "tokens": 16},
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        # 3. Reload state and run reconciliation
        reloaded = MemoryState(self.mem_dir)
        self.assertEqual(reloaded.current_seq, 5)

        changed = reloaded.reconcile_with_journal()
        self.assertTrue(changed)
        self.assertEqual(reloaded.current_seq, 8)
        self.assertEqual(reloaded.total_events, 8)

        # 4. Next sequence should continue contiguously from 9
        next_s = reloaded.next_seq(provider="antigravity")
        self.assertEqual(next_s, 9)

    def test_manual_acknowledgement_strictly_bounded_by_pending_checkpoint(self):
        """Acknowledging beyond the pending compaction checkpoint requires explicit force=True."""
        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 100
        state.data["total_events"] = 100
        state.data["total_lifetime_tokens"] = 100000
        state.request_compaction(seq=50, uncompacted_tokens=50000)
        state.save()

        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.pending_compaction["requested_at_seq"], 50)

        # 1. Calling with no argument automatically acknowledges the requested checkpoint (50)
        state_copy = MemoryState(self.mem_dir)
        # Attempting to acknowledge beyond 50 without force must fail
        with self.assertRaises(ValueError) as ctx:
            state_copy.acknowledge_compaction(seq=100, force=False)
        self.assertIn("beyond pending compaction checkpoint #50", str(ctx.exception))
        self.assertIn("force=True", str(ctx.exception))

        # 2. Acknowledging with force=True succeeds
        success = state_copy.acknowledge_compaction(seq=100, force=True)
        self.assertTrue(success)
        self.assertEqual(state_copy.last_compacted_seq, 100)
        self.assertFalse(state_copy.has_pending_compaction())

        # 3. Acknowledging ahead of current_seq (100) must fail even with force=True
        with self.assertRaises(ValueError) as ctx2:
            state_copy.acknowledge_compaction(seq=150, force=True)
        self.assertIn("ahead of current sequence #100", str(ctx2.exception))

    def test_state_save_failure_raises_exception_without_silent_swallowing(self):
        """State.save() does not swallow write errors with pass."""
        state = MemoryState(self.mem_dir)
        # Mock NamedTemporaryFile to simulate write / disk failure
        with patch("tempfile.NamedTemporaryFile", side_effect=OSError("Disk write error")):
            with self.assertRaises(OSError):
                state.save()


if __name__ == "__main__":
    unittest.main()
