"""Unit tests for cross-process file locking."""

import shutil
import tempfile
import unittest
from pathlib import Path

from memory_hook.locking import FileLock, LockTimeoutError


class TestFileLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.lock_path = Path(self.test_dir) / ".lock"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_single_process_lock_acquire_and_release(self):
        lock = FileLock(self.lock_path, timeout=1.0)
        with lock:
            self.assertTrue(self.lock_path.is_file())
        # Released without errors

    def test_concurrent_lock_contention_and_timeout(self):
        lock1 = FileLock(self.lock_path, timeout=1.0)
        lock1.acquire()

        try:
            # Second lock should time out quickly
            lock2 = FileLock(self.lock_path, timeout=0.1, poll_interval=0.02)
            with self.assertRaises(LockTimeoutError):
                lock2.acquire()
        finally:
            lock1.release()

        # After releasing lock1, lock2 should acquire immediately
        lock2 = FileLock(self.lock_path, timeout=1.0)
        with lock2:
            pass


if __name__ == "__main__":
    unittest.main()
