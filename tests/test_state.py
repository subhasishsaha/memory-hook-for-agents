import tempfile
import unittest
from pathlib import Path

from memory_hook.state import MemoryState


class TestMemoryState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mem_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_initialization(self):
        state = MemoryState(self.mem_dir)
        self.assertEqual(state.current_seq, 0)
        self.assertEqual(state.last_compacted_seq, 0)
        self.assertFalse(state.is_compaction_due(10))

    def test_state_increment_and_persistence(self):
        state = MemoryState(self.mem_dir)
        seq1 = state.next_seq(provider="antigravity")
        self.assertEqual(seq1, 1)

        seq2 = state.next_seq(provider="claude-code")
        self.assertEqual(seq2, 2)

        # Reload from disk
        reloaded = MemoryState(self.mem_dir)
        self.assertEqual(reloaded.current_seq, 2)
        self.assertEqual(reloaded.data.get("last_updated_provider"), "claude-code")

    def test_compaction_threshold(self):
        state = MemoryState(self.mem_dir)
        for _ in range(9):
            state.next_seq()
        self.assertFalse(state.is_compaction_due(10))

        state.next_seq()  # 10th
        self.assertTrue(state.is_compaction_due(10))

        # Mark compacted
        state.mark_compacted(10)
        self.assertFalse(state.is_compaction_due(10))
        self.assertEqual(state.last_compacted_seq, 10)


if __name__ == "__main__":
    unittest.main()
