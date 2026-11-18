"""Regression tests for pending compaction preservation during short.log pruning."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import SHORT_LOG_NAME
from memory_hook.runner import prune_short_log
from memory_hook.standalone_template import STANDALONE_RUNNER_CODE
from memory_hook.state import MemoryState


class TestPruningRegression(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)
        self.mem_dir = self.root / ".memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self.short_log = self.mem_dir / SHORT_LOG_NAME

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _generate_events(self, count: int, tokens_per_event: int = 10000):
        lines = []
        for i in range(1, count + 1):
            rec = {
                "seq": i,
                "ts": "2026-11-18T00:00:00Z",
                "provider": "antigravity",
                "type": "user_request",
                "tokens": tokens_per_event,
                "payload": {"content": f"Event {i}"},
            }
            lines.append(json.dumps(rec) + "\n")
        self.short_log.write_text("".join(lines), encoding="utf-8")

    def test_uncompacted_events_never_pruned_even_when_exceeding_token_limit(self):
        """Regression test: Uncompacted events awaiting synthesis must NEVER be deleted."""
        # 25 events * 10k tokens = 250k tokens (exceeds 100k token limit)
        self._generate_events(count=25, tokens_per_event=10000)

        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 25
        state.data["last_compacted_seq"] = 0
        state.data["short_log_tokens"] = 250000
        state.data["total_lifetime_tokens"] = 250000
        state.request_compaction(seq=5, uncompacted_tokens=50000)
        state.save()

        # Run pruning
        prune_short_log(self.mem_dir, state, max_tokens=100000)

        # Invariant: ALL 25 events must survive because last_compacted_seq = 0
        lines = [l for l in self.short_log.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.assertEqual(len(lines), 25)
        first_event = json.loads(lines[0])
        self.assertEqual(first_event["seq"], 1)
        last_event = json.loads(lines[-1])
        self.assertEqual(last_event["seq"], 25)

    def test_pruning_protects_uncompacted_tail_after_partial_compaction(self):
        """Compacted events are pruned while uncompacted events are 100% retained."""
        # 20 events * 10k tokens = 200k tokens
        self._generate_events(count=20, tokens_per_event=10000)

        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 20
        # Events #1..#5 have been compacted; events #6..#20 (15 events = 150k tokens) are uncompacted
        state.data["last_compacted_seq"] = 5
        state.data["short_log_tokens"] = 200000
        state.data["total_lifetime_tokens"] = 200000
        state.save()

        # Prune with 100k token limit
        prune_short_log(self.mem_dir, state, max_tokens=100000)

        lines = [l for l in self.short_log.read_text(encoding="utf-8").split("\n") if l.strip()]
        # Compacted events (#1..#5) should be pruned because uncompacted events alone take 150k > 100k
        # All uncompacted events (#6..#20) must be preserved
        self.assertEqual(len(lines), 15)
        first_event = json.loads(lines[0])
        self.assertEqual(first_event["seq"], 6)
        last_event = json.loads(lines[-1])
        self.assertEqual(last_event["seq"], 20)

    def test_pruning_normalizes_to_100k_after_full_compaction(self):
        """Once all events are acknowledged as compacted, rolling window strictly enforces max_tokens."""
        # 20 events * 10k tokens = 200k tokens
        self._generate_events(count=20, tokens_per_event=10000)

        state = MemoryState(self.mem_dir)
        state.data["current_seq"] = 20
        # All 20 events are compacted
        state.data["last_compacted_seq"] = 20
        state.data["short_log_tokens"] = 200000
        state.data["total_lifetime_tokens"] = 200000
        state.save()

        # Prune with 100k token limit
        prune_short_log(self.mem_dir, state, max_tokens=100000)

        lines = [l for l in self.short_log.read_text(encoding="utf-8").split("\n") if l.strip()]
        # Exactly newest 10 events should remain (10 * 10k = 100k tokens)
        self.assertEqual(len(lines), 10)
        first_event = json.loads(lines[0])
        self.assertEqual(first_event["seq"], 11)
        last_event = json.loads(lines[-1])
        self.assertEqual(last_event["seq"], 20)


if __name__ == "__main__":
    unittest.main()
