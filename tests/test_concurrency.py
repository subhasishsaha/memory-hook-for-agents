"""Multiprocessing concurrency tests validating write transactions under parallel processes."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memory_hook.constants import SHORT_LOG_NAME
from memory_hook.state import MemoryState


def _worker_write_events(project_root_str: str, worker_id: int, num_events: int) -> int:
    """Worker function executed in separate process to append events via runner."""
    from memory_hook.runner import run_hook
    root = Path(project_root_str)
    success_count = 0
    for i in range(num_events):
        payload = json.dumps({
            "worker_id": worker_id,
            "event_idx": i,
            "content": f"Worker {worker_id} payload message {i}",
        })
        resp = run_hook(
            event_hint="UserPromptSubmit",
            provider_hint="claude-code",
            raw_input_data=payload,
            project_root=root,
        )
        if resp.get("status") == "ok":
            success_count += 1
    return success_count


class TestMultiprocessingConcurrency(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)
        self.mem_dir = self.root / ".memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_concurrent_multiprocess_writes_no_duplicate_sequences(self):
        from concurrent.futures import ProcessPoolExecutor

        num_workers = 4
        events_per_worker = 10
        total_expected_events = num_workers * events_per_worker

        # Launch 4 concurrent worker processes
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(
                    _worker_write_events,
                    str(self.root),
                    w_id,
                    events_per_worker,
                )
                for w_id in range(num_workers)
            ]
            results = [f.result() for f in futures]

        # Verify all workers succeeded
        self.assertEqual(results, [events_per_worker] * num_workers)

        # Inspect short.log
        short_log = self.mem_dir / SHORT_LOG_NAME
        self.assertTrue(short_log.is_file())

        with open(short_log, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]

        # 1. Exact total event count
        self.assertEqual(len(lines), total_expected_events)

        # 2. Sequence numbers must be strictly 1..total_expected_events with ZERO duplicates
        seqs = [rec["seq"] for rec in lines]
        self.assertEqual(len(seqs), len(set(seqs)), "Sequence numbers contained collisions!")
        self.assertEqual(seqs, list(range(1, total_expected_events + 1)))

        # 3. State file counters must accurately match
        state = MemoryState(self.mem_dir)
        self.assertEqual(state.current_seq, total_expected_events)
        self.assertEqual(state.total_events, total_expected_events)


if __name__ == "__main__":
    unittest.main()
