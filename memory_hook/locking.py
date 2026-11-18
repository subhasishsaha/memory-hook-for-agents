"""Cross-process file locking for memory-hook using standard library only."""

import os
import sys
import time
from pathlib import Path
from typing import Optional


class LockTimeoutError(Exception):
    """Raised when file lock cannot be acquired within the timeout period."""
    pass


class FileLock:
    """A cross-platform, reentrant-safe file lock supporting Windows and Unix.
    
    Uses msvcrt on Windows and fcntl on Unix. Pure standard library.
    """

    def __init__(self, lock_file: Path, timeout: float = 5.0, poll_interval: float = 0.02):
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd: Optional[int] = None
        self._file_obj = None

    def acquire(self) -> "FileLock":
        """Acquires exclusive lock, retrying until timeout."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()

        while True:
            try:
                # Open with append/read-write mode without truncating
                self._file_obj = open(self.lock_file, "a+", encoding="utf-8")
                self._fd = self._file_obj.fileno()

                if sys.platform == "win32":
                    import msvcrt
                    # Lock first byte non-blocking
                    self._file_obj.seek(0)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    # Exclusive non-blocking lock
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Successfully acquired
                return self

            except (IOError, OSError):
                # Lock is held by another process
                if self._file_obj:
                    try:
                        self._file_obj.close()
                    except Exception:
                        pass
                    self._file_obj = None
                    self._fd = None

                elapsed = time.monotonic() - start_time
                if elapsed >= self.timeout:
                    raise LockTimeoutError(
                        f"Timed out after {self.timeout}s waiting for lock: {self.lock_file}"
                    )
                time.sleep(self.poll_interval)

    def release(self) -> None:
        """Releases the held lock."""
        if self._file_obj and self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._file_obj.seek(0)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                try:
                    self._file_obj.close()
                except Exception:
                    pass
                self._file_obj = None
                self._fd = None

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
