"""Unit tests for compaction acknowledgement protocol."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import LONG_LOG_NAME, SHORT_LOG_NAME
from memory_hook.runner import run_hook
from memory_hook.state import MemoryState


class TestCompactionAcknowledgement(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)
        self.mem_dir = self.root / ".memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_pending_request_lifecycle(self):
        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 20
        state.save()
        self.assertFalse(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 0)

        # 1. Request compaction
        req = state.request_compaction(seq=15, uncompacted_tokens=12000)
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(req["status"], "pending")
        self.assertEqual(req["requested_at_seq"], 15)
        self.assertEqual(req["requested_tokens"], 12000)
        # Checkpoint MUST NOT advance yet
        self.assertEqual(state.last_compacted_seq, 0)

        # 2. Reload state from disk to confirm persistence
        state_reloaded = MemoryState(self.mem_dir)
        self.assertTrue(state_reloaded.has_pending_compaction())
        self.assertEqual(state_reloaded.last_compacted_seq, 0)

        # 3. Duplicate request should be idempotent and preserve original request seq
        req2 = state_reloaded.request_compaction(seq=20, uncompacted_tokens=15000)
        self.assertEqual(req2["requested_at_seq"], 15)

        # 4. Acknowledge compaction
        ack_res = state_reloaded.acknowledge_compaction(15)
        self.assertTrue(ack_res)
        self.assertFalse(state_reloaded.has_pending_compaction())
        self.assertEqual(state_reloaded.last_compacted_seq, 15)

        # 5. Repeated acknowledgement should be safe and idempotent
        ack_res_repeat = state_reloaded.acknowledge_compaction(15)
        self.assertFalse(ack_res_repeat)
        self.assertEqual(state_reloaded.last_compacted_seq, 15)

    def test_automatic_long_log_sync_detection(self):
        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 50
        state.save()
        state.request_compaction(seq=50, uncompacted_tokens=10500)
        long_log_path = self.mem_dir / LONG_LOG_NAME

        # Missing or empty long.log must not acknowledge
        self.assertFalse(state.check_long_log_sync(long_log_path))
        self.assertTrue(state.has_pending_compaction())

        # long.log with earlier sequence must not acknowledge
        long_log_path.write_text(
            "# Project Memory\n> Last Updated: 2026-11-18 | Synced Through Event: #30\n",
            encoding="utf-8",
        )
        self.assertFalse(state.check_long_log_sync(long_log_path))
        self.assertTrue(state.has_pending_compaction())

        # long.log synced through event #50 or higher must acknowledge
        long_log_path.write_text(
            "# Project Memory\n> Last Updated: 2026-11-18 | Synced Through Event: #50\n",
            encoding="utf-8",
        )
        self.assertTrue(state.check_long_log_sync(long_log_path))
        self.assertFalse(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 50)

    def test_unacknowledged_compaction_re_surfaces_on_next_session(self):
        # Accumulate 10k tokens
        for i in range(8):
            run_hook(
                event_hint="PreToolUse",
                provider_hint="antigravity",
                raw_input_data=json.dumps({
                    "toolCall": {"name": "write_file", "args": {"content": "X" * 6000}},
                    "stepIdx": i,
                }),
                project_root=self.root,
            )

        # Session 1: PreInvocation gets notice
        resp1 = run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=json.dumps({"lastUserInput": "Hello session 1"}),
            project_root=self.root,
        )
        self.assertIn("injectSteps", resp1)
        self.assertIn("[MEMORY HOOK UPDATE NOTICE]", resp1["injectSteps"][0]["systemMessage"]["systemMessage"])

        state = MemoryState(self.mem_dir)
        self.assertTrue(state.has_pending_compaction())
        self.assertEqual(state.last_compacted_seq, 0)

        # Simulate agent did NOT update long.log, and next invocation starts
        resp2 = run_hook(
            event_hint="PreInvocation",
            provider_hint="antigravity",
            raw_input_data=json.dumps({"lastUserInput": "Hello session 2"}),
            project_root=self.root,
        )
        # Notice must re-surface because compaction is still unacknowledged!
        self.assertIn("injectSteps", resp2)
        self.assertIn("[MEMORY HOOK UPDATE NOTICE]", resp2["injectSteps"][0]["systemMessage"]["systemMessage"])


if __name__ == "__main__":
    unittest.main()
