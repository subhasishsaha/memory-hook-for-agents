"""Cross-process file locking for memory-hook using standard library only."""

import os
import sys
import time
from pathlib import Path
from typing import Optional


class LockTimeoutError(Exception):
    """Raised when file lock cannot be acquired within the timeout period."""
    pass


def safe_fsync(fd: int) -> None:
    """Durably flushes file descriptor to disk, propagating genuine I/O failures.

    Ignores only platform-specific non-fatal errno codes where fsync is not supported
    by the underlying device (e.g. character devices, pipes, or virtual filesystems).
    """
    try:
        os.fsync(fd)
    except OSError as e:
        import errno
        unsupported_codes = {
            getattr(errno, "EINVAL", 22),
            getattr(errno, "ENOTSUP", 45),
            getattr(errno, "EOPNOTSUPP", 45),
        }
        if e.errno in unsupported_codes:
            return
        raise


def safe_fsync_dir(dir_path: Path) -> None:
    """Durably flushes parent directory descriptor to disk on POSIX systems.

    On Windows, directory descriptors do not support fsync, so this is a no-op.
    On POSIX, opening the directory with O_RDONLY and calling fsync ensures atomic rename/replace
    directory-entry updates are durably committed to media.
    """
    if sys.platform == "win32":
        return
    try:
        dir_fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            safe_fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as e:
        import errno
        unsupported_codes = {
            getattr(errno, "EINVAL", 22),
            getattr(errno, "ENOTSUP", 45),
            getattr(errno, "EOPNOTSUPP", 45),
        }
        if e.errno in unsupported_codes:
            return
        raise


class FileLock:
    """A cross-platform, reentrant-safe file lock supporting Windows and Unix.
    
    Uses msvcrt on Windows and fcntl on Unix. Pure standard library.
    """

    def __init__(self, lock_file: Path, timeout: float = 5.0, poll_interval: float = 0.02):
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd: Optional[int] = None

    def acquire(self) -> "FileLock":
        """Acquires exclusive lock, retrying until timeout."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()

        while True:
            try:
                # Open low-level OS file descriptor directly
                flags = os.O_RDWR | os.O_CREAT
                self._fd = os.open(str(self.lock_file), flags, 0o666)

                if sys.platform == "win32":
                    import msvcrt
                    # Lock first byte non-blocking
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    # Exclusive non-blocking lock
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Successfully acquired
                return self

            except (IOError, OSError):
                # Lock is held by another process
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except Exception:
                        pass
                    self._fd = None

                elapsed = time.monotonic() - start_time
                if elapsed >= self.timeout:
                    raise LockTimeoutError(
                        f"Timed out after {self.timeout}s waiting for lock: {self.lock_file}"
                    )
                time.sleep(self.poll_interval)

            except BaseException:
                # Deterministic cleanup on interrupt or unexpected abort
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except Exception:
                        pass
                    self._fd = None
                raise

    def release(self) -> None:
        """Releases the held lock."""
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None

    def __del__(self) -> None:
        """Destructor to ensure lock release if object is garbage-collected."""
        try:
            self.release()
        except Exception:
            pass

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
