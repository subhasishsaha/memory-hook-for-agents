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

    def test_compaction_acknowledgement_token_accounting_invariant(self):
        """Acknowledging an earlier sequence checkpoint must only mark tokens through that checkpoint as compacted."""
        state = MemoryState(self.mem_dir)
        # Generate 100 events, 1000 tokens each (total 100,000 tokens)
        records = []
        for i in range(1, 101):
            records.append({
                "seq": i,
                "ts": "2026-11-18T00:00:00Z",
                "provider": "antigravity",
                "tokens": 1000,
                "payload": {"content": f"Event {i}"}
            })
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        state.data["current_seq"] = 100
        state.data["total_events"] = 100
        state.data["total_lifetime_tokens"] = 100000
        state.data["short_log_tokens"] = 100000
        state.data["last_compacted_seq"] = 0
        state.data["last_compacted_tokens"] = 0

        # Compaction requested at #50 (representing 50,000 cumulative tokens)
        state.request_compaction(seq=50, uncompacted_tokens=50000)
        self.assertEqual(state.pending_compaction["lifetime_tokens_at_request"], 50000)

        # Agent acknowledges #50
        state.acknowledge_compaction(seq=50)

        # Invariant checks:
        self.assertEqual(state.last_compacted_seq, 50)
        # last_compacted_tokens must be 50,000, NOT the current lifetime total (100,000)!
        self.assertEqual(state.last_compacted_tokens, 50000)
        # The remaining 50,000 tokens from events #51..#100 must remain uncompacted!
        self.assertEqual(state.uncompacted_tokens, 50000)

    def test_check_long_log_sync_satisfies_checkpoint_even_if_oversynced(self):
        """When an agent updates long.log through a sequence >= requested checkpoint, sync is accepted."""
        from memory_hook.constants import LONG_LOG_NAME

        state = MemoryState(self.mem_dir)
        records = [
            {"seq": i, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 100}
            for i in range(1, 101)
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        state.data["current_seq"] = 100
        state.data["total_events"] = 100
        state.data["total_lifetime_tokens"] = 10000
        state.data["short_log_tokens"] = 10000
        # Request created at #50
        state.request_compaction(seq=50, uncompacted_tokens=5000)
        state.save()

        # Agent writes long.log synced through event #100 (more than requested #50)
        long_log_path = self.mem_dir / LONG_LOG_NAME
        long_log_path.write_text(
            "# Project Memory\n> Last Updated: 2026-11-18 | Synced Through Event: #100\n",
            encoding="utf-8"
        )

        synced = state.check_long_log_sync(long_log_path)
        self.assertTrue(synced)
        self.assertFalse(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 100)
        self.assertEqual(state.last_compacted_tokens, 10000)

    def test_crash_recovery_before_journal_append_rolls_back_phantom_seq(self):
        """If a crash occurred where state recorded sequence 11 but short.log only has 10, rolls back cleanly."""
        state = MemoryState(self.mem_dir)
        records = [
            {"seq": i, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 10}
            for i in range(1, 11)
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        # Phantom sequence in state
        state.data["current_seq"] = 11
        state.data["total_events"] = 11
        state.save()

        # Reconciliation rolls back current_seq to 10
        changed = state.reconcile_with_journal()
        self.assertTrue(changed)
        self.assertEqual(state.current_seq, 10)

    def test_crash_recovery_after_journal_append_reconciles_tokens_idempotently(self):
        """If journal append succeeded with 50 tokens but state.save was interrupted, tokens are restored."""
        state = MemoryState(self.mem_dir)
        records = [
            {"seq": 1, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 10},
            {"seq": 2, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 50},
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        # State only accounted for event 1
        state.data["current_seq"] = 1
        state.data["short_log_tokens"] = 10
        state.data["total_lifetime_tokens"] = 10
        state.save()

        # Run reconciliation
        changed = state.reconcile_with_journal()
        self.assertTrue(changed)
        self.assertEqual(state.current_seq, 2)
        self.assertEqual(state.short_log_tokens, 60)
        self.assertEqual(state.total_lifetime_tokens, 60)

        # Running reconciliation again should be a safe no-op
        changed_again = state.reconcile_with_journal()
        self.assertFalse(changed_again)
        self.assertEqual(state.short_log_tokens, 60)
        self.assertEqual(state.total_lifetime_tokens, 60)

    def test_acknowledgement_lower_bound_validation_and_preservation_of_pending_request(self):
        """Acknowledging a sequence below the requested checkpoint cannot discard the pending request."""
        state = MemoryState(self.mem_dir)
        records = [
            {"seq": i, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 100}
            for i in range(1, 101)
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        state.data["current_seq"] = 100
        state.data["total_events"] = 100
        state.data["total_lifetime_tokens"] = 10000
        state.data["short_log_tokens"] = 10000
        state.data["last_compacted_seq"] = 50
        state.data["last_compacted_tokens"] = 5000

        # Pending compaction created at #100
        state.request_compaction(seq=100, uncompacted_tokens=5000)
        self.assertTrue(state.has_pending_compaction())

        # 1. Unforced acknowledgement below checkpoint #100 must be rejected
        with self.assertRaises(ValueError) as ctx:
            state.acknowledge_compaction(seq=60, force=False)
        self.assertIn("below active pending compaction checkpoint #100", str(ctx.exception))
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 50)

        # 2. Forced acknowledgement of #60 advances sequence, but MUST NOT discard pending compaction #100
        success = state.acknowledge_compaction(seq=60, force=True)
        self.assertTrue(success)
        self.assertEqual(state.last_compacted_seq, 60)
        # Invariant: compaction through #100 is still unfinished and must remain pending!
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.pending_compaction["requested_at_seq"], 100)

        # 3. An acknowledgement older than last_compacted_seq (e.g. #40) is a no-op and never clears pending state
        noop_res = state.acknowledge_compaction(seq=40, force=True)
        self.assertFalse(noop_res)
        self.assertEqual(state.last_compacted_seq, 60)
        self.assertTrue(state.has_pending_compaction())

        # 4. Acknowledging #100 satisfies the checkpoint and finally clears pending compaction
        complete_res = state.acknowledge_compaction(seq=100)
        self.assertTrue(complete_res)
        self.assertEqual(state.last_compacted_seq, 100)
        self.assertFalse(state.has_pending_compaction())

    def test_check_long_log_sync_rejects_marker_ahead_of_current_sequence_package_and_standalone(self):
        """Both package and standalone runners reject sync markers claiming sequences ahead of current journal."""
        from memory_hook.constants import LONG_LOG_NAME
        from memory_hook.standalone_template import STANDALONE_RUNNER_CODE

        # Set up state with current_seq = 100, pending request = 80
        state = MemoryState(self.mem_dir)
        records = [
            {"seq": i, "ts": "2026-11-18T00:00:00Z", "provider": "antigravity", "tokens": 100}
            for i in range(1, 101)
        ]
        self.short_log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        state.data["current_seq"] = 100
        state.data["total_events"] = 100
        state.data["total_lifetime_tokens"] = 10000
        state.data["short_log_tokens"] = 10000
        state.request_compaction(seq=80, uncompacted_tokens=8000)
        state.save()

        # long.log claims future sequence #101
        long_log_path = self.mem_dir / LONG_LOG_NAME
        long_log_path.write_text(
            "# Project Memory\n> Last Updated: 2026-11-18 | Synced Through Event: #101\n",
            encoding="utf-8"
        )

        # 1. Package check_long_log_sync must reject future marker
        pkg_synced = state.check_long_log_sync(long_log_path)
        self.assertFalse(pkg_synced)
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 0)

        # 2. Standalone runner must also reject future marker
        runner_scope = {}
        exec(STANDALONE_RUNNER_CODE, runner_scope)
        std_check_sync = runner_scope["check_long_log_sync"]
        std_load_state = runner_scope["load_state"]

        std_state = std_load_state(self.mem_dir)
        std_synced = std_check_sync(self.mem_dir, std_state)
        self.assertFalse(std_synced)
        self.assertIsNotNone(std_state.get("pending_compaction"))
        self.assertEqual(std_state.get("last_compacted_seq", 0), 0)

    def test_recovery_metadata_preserved_across_reloads(self):
        """Recovery metadata fields (retained_events, lifetime_tokens_exact, recovered_at_ts) persist on reload."""
        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 50
        state.data["retained_events"] = 50
        state.data["lifetime_tokens_exact"] = False
        state.data["recovered_at_ts"] = "2026-11-18T10:00:00Z"
        state.save()

        # Reload from disk
        reloaded = MemoryState(self.mem_dir)
        self.assertEqual(reloaded.data.get("retained_events"), 50)
        self.assertIs(reloaded.data.get("lifetime_tokens_exact"), False)
        self.assertEqual(reloaded.data.get("recovered_at_ts"), "2026-11-18T10:00:00Z")

    def test_safe_fsync_error_propagation_and_unsupported_errno(self):
        """safe_fsync propagates fatal disk I/O errors and ignores only unsupported platform errnos."""
        import errno
        from memory_hook.locking import safe_fsync

        # 1. Genuine fatal I/O failure must be raised
        with patch("os.fsync", side_effect=OSError(getattr(errno, "EIO", 5), "Input/output error")):
            with self.assertRaises(OSError) as ctx:
                safe_fsync(999)
            self.assertEqual(ctx.exception.errno, getattr(errno, "EIO", 5))

        # 2. Unsupported device errno (e.g. EINVAL on raw devices / character streams) should be safely ignored
        with patch("os.fsync", side_effect=OSError(getattr(errno, "EINVAL", 22), "Invalid argument")):
            # Should not raise
            safe_fsync(999)


if __name__ == "__main__":
    unittest.main()
