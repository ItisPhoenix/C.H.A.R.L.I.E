"""Cross-process single-instance ownership for the Charlie runtime.

Two live Charlie instances are not a degraded-but-working configuration; they are
a silent failure. Each instance runs its own Telegram poller, and Telegram serves
one long-poll per bot token, so the second process receives
``Conflict: terminated by other getUpdates request`` and stops receiving
updates. The Python-Telegram-Bot library reports that at DEBUG and retries
forever, so the losing instance logs almost nothing, keeps serving its console,
and still publishes ``HealthStatus.RUNNING`` for ``telegram``. It looks healthy
while delivering nothing. The ZMQ event publisher on the canonical port fails the
same way (``Address in use``) for the same underlying reason, but that is a
*symptom* of the missing single-instance contract rather than the contract
itself, and the publisher must keep binding because other tooling observes it.

This module owns that contract: exactly one process may hold the runtime, and a
loser reports the conflict and exits instead of degrading in silence.
"""

from __future__ import annotations

import os
import sys
from typing import Final, Optional

DEFAULT_LOCK_PATH: Final[str] = os.path.join("data", "charlie.lock")

# Byte 0 of the lock file is the lock sentinel. The holder's PID record starts
# at byte 1 so that reading it never intersects the locked range (see
# ``_read_holder_pid`` for why that matters on Windows).
_LOCK_OFFSET: Final[int] = 0
_PID_OFFSET: Final[int] = 1
_PID_PREFIX: Final[bytes] = b"charlie-pid="
# Fixed-width record: a shorter PID overwriting a longer one must never leave
# trailing digits behind that could be misread as part of the value.
_PID_DIGITS: Final[int] = 12
# os.open() maps to the CRT _wopen on Windows, and CRT text mode (the default
# when O_BINARY is absent) rewrites "\n" as "\r\n" on write. That silently
# shifts every byte after the first by one, breaking the sentinel/PID offsets
# this file depends on, so the descriptor must be forced to binary. POSIX has
# no such flag and no such translation, hence the getattr.
_BINARY_FLAG: Final[int] = getattr(os, "O_BINARY", 0)


class SingleInstanceError(RuntimeError):
    """Raised when another live process already owns the runtime lock."""

    def __init__(self, path: str, holder_pid: Optional[int]) -> None:
        self.path = path
        self.holder_pid = holder_pid
        if holder_pid is None:
            holder = "another Charlie process"
        else:
            holder = f"PID {holder_pid}"
        super().__init__(
            f"Charlie is already running: {path} is locked by {holder}. "
            "Only one instance may own the Telegram poller and the event bus, "
            "otherwise the second instance silently delivers nothing. Stop the "
            "other instance and start this one again. No manual cleanup is "
            "needed after a crash: the operating system releases the lock when "
            "the owning process dies, so leaving this file on disk is harmless."
        )


def _format_pid_record(pid: int) -> bytes:
    return _PID_PREFIX + f"{pid:0{_PID_DIGITS}d}".encode("ascii") + b"\n"


def _try_lock_exclusive(fd: int) -> None:
    """Take a *non-blocking* exclusive lock on the sentinel byte of ``fd``.

    Non-blocking is the whole point: a blocking lock would park the losing
    instance on a syscall forever instead of reporting the conflict and exiting.
    Both backends raise ``OSError`` when the range is already locked, which is
    the only reliable contention signal.
    """
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_exclusive(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _read_holder_pid(path: str) -> Optional[int]:
    """Best-effort read of the current holder's PID, for the failure message.

    The read deliberately starts at ``_PID_OFFSET`` instead of byte 0. Windows
    byte-range locks are *mandatory*: a ``ReadFile`` that overlaps the locked
    range fails with ``PermissionError`` even for a reader with no write intent.
    Reading only the bytes past the sentinel therefore succeeds where reading
    from offset 0 would be refused by the kernel. A missing or unparsable record
    is reported as "unknown" rather than guessed, because the recorded PID is
    advisory -- a crashed predecessor can leave a stale one behind.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(_PID_OFFSET)
            raw = handle.read(len(_PID_PREFIX) + _PID_DIGITS + 1)
    except OSError:
        return None
    if not raw.startswith(_PID_PREFIX):
        return None
    digits = raw[len(_PID_PREFIX) :].strip()
    try:
        return int(digits)
    except ValueError:
        return None


class SingleInstanceLock:
    """Exclusive cross-process ownership of the Charlie runtime.

    Why a plain file handle is sufficient, and why no PID-liveness check is
    needed: both ``msvcrt.locking`` (Windows) and ``fcntl.flock`` (POSIX) are
    kernel-managed locks attached to the *open file description*, not to the
    file's contents and not to its existence on disk. The kernel drops them when
    the owning process terminates for any reason -- normal return, unhandled
    exception, ``TerminateProcess``, power loss. A crash can therefore leave the
    lock *file* behind containing a stale PID, but it can never leave the lock
    *held*, and the next run acquires it normally. Contention is decided solely
    by whether the locking syscall succeeds.

    The recorded PID is consequently never trusted for correctness; it exists
    only to make the failure message actionable.

    The lock file must not be deleted to "unstick" a running instance. Unlinking
    a path that a live holder has locked does not release the holder's lock; the
    next process would create a *new* inode, lock that successfully, and run a
    second Charlie beside the first -- the exact failure this module prevents.
    """

    def __init__(self, path: str = DEFAULT_LOCK_PATH) -> None:
        self.path = path
        self._fd: Optional[int] = None

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    def acquire(self) -> SingleInstanceLock:
        """Claim the runtime, or raise :class:`SingleInstanceError`."""
        if self._fd is not None:
            raise RuntimeError(f"Lock already acquired by this object: {self.path}")
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        # Raw descriptor plus os-level I/O only. Mixing a buffered file object
        # with os.lseek on the same descriptor would let the buffer's notion of
        # the position drift away from the real one, and the lock is taken at
        # the real position.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | _BINARY_FLAG, 0o644)
        try:
            if os.fstat(fd).st_size <= _PID_OFFSET:
                os.write(fd, b"\0")
            _try_lock_exclusive(fd)
        except OSError as exc:
            os.close(fd)
            raise SingleInstanceError(self.path, _read_holder_pid(self.path)) from exc
        self._fd = fd
        self._record_holder(os.getpid())
        return self

    def _record_holder(self, pid: int) -> None:
        """Publish the winner's PID for the next contender to report.

        Written after the lock is held, so a contender can only ever read a
        completed record from a live owner. Failure here must not fail the
        acquisition: the lock is already ours and the run is still correct
        without the informational PID.
        """
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, _PID_OFFSET, os.SEEK_SET)
            os.write(self._fd, _format_pid_record(pid))
        except OSError:
            pass

    def release(self) -> None:
        """Drop ownership. Safe to call repeatedly, and safe during shutdown."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock_exclusive(fd)
        except OSError:
            # Best-effort only. If the explicit unlock fails, process exit still
            # closes the descriptor and the kernel drops the lock, so raising
            # here would only turn a clean shutdown into a failed one.
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> SingleInstanceLock:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
