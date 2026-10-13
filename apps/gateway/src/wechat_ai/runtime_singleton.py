"""Single-instance port lock for ``wechat-ai serve`` (P1-2).

``cli.serve`` previously relied only on an HTTP identity probe
(``_existing_runtime_status``), which has a start-up race: a second instance can
probe the port while the first instance is still binding uvicorn, read "absent",
and start too -- two gateways then write the same SQLite file and immediately hit
``database is locked``.

This module adds a deterministic, pid/OS-file lock on top of that HTTP probe:

* **atomicity** -- an OS-level non-blocking byte-range lock (``msvcrt`` on
  Windows, ``fcntl`` elsewhere) that the OS releases automatically when the
  holding process exits, even on crash/kill.
* **diagnostics / stale takeover** -- the lock file also records the owner pid;
  if the lock is free but the recorded pid is dead (crashed and left the file
  behind), a fresh instance takes over instead of refusing.

Three observable states (covered by unit tests):

* no lock file -> acquire and start;
* lock held by a live process (OS lock busy) -> :class:`PortLockHeld`, refuse;
* lock file present but owner pid dead -> take over and start.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import BinaryIO

_LOGGER = logging.getLogger(__name__)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259


class PortLockHeld(RuntimeError):
    """Raised when a live process already holds the single-instance lock."""

    def __init__(self, owner_pid: int | None, path: Path) -> None:
        self.owner_pid = owner_pid
        self.path = path
        super().__init__(f"another ChatOS instance holds {path} (pid={owner_pid})")


class PortLock:
    """An acquired single-instance lock; release on shutdown."""

    def __init__(self, path: Path, stream: BinaryIO, owner_pid: int) -> None:
        self._path = path
        self._stream = stream
        self._owner_pid = owner_pid
        self.released = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def owner_pid(self) -> int:
        return self._owner_pid

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        stream = self._stream
        try:
            # msvcrt requires unlocking the same region at the same position; we
            # wrote the pid after locking, so rewind to byte 0 first.
            stream.seek(0)
            _unlock_stream(stream)
        finally:
            stream.close()
            # Best-effort: remove our own marker so a later start sees "absent".
            # The OS-level lock above already prevents double-start; this is only
            # cosmetic and must never raise out of shutdown.
            try:
                self._path.unlink()
            except OSError:
                pass


def port_lock_path(project_root: str | Path, port: int) -> Path:
    """Canonical lock location: ``<project_root>/var/run/wechat_ai-<port>.pid``."""

    return Path(project_root) / "var" / "run" / f"wechat_ai-{port}.pid"


def read_owner_pid(path: str | Path) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def process_is_alive(pid: int) -> bool:
    """Return whether a process with this pid is currently running."""

    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER means no such process; any other error (e.g.
        # ERROR_ACCESS_DENIED) means the process exists but we lack rights.
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def acquire_port_lock(path: str | Path, *, pid: int | None = None) -> PortLock:
    """Acquire the single-instance lock, taking over stale locks.

    :raises PortLockHeld: if a live process already holds the lock.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    own_pid = os.getpid() if pid is None else pid
    stream = path.open("a+b")
    # msvcrt/fcntl lock byte 0, so the file must hold at least one byte.
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        _lock_stream(stream)
    except OSError:
        stream.close()
        raise PortLockHeld(read_owner_pid(path), path)
    # We now hold the OS lock. A live holder would still hold the OS lock, so the
    # recorded pid (if any) must be stale -- unless it is us.
    prior = read_owner_pid(path)
    if prior is not None and prior != own_pid and process_is_alive(prior):
        # Defensive: should be unreachable because the OS lock was free, but never
        # double-start a live instance we somehow discovered via the pid file.
        _unlock_stream(stream)
        stream.close()
        raise PortLockHeld(prior, path)
    if prior is not None and prior != own_pid:
        _LOGGER.warning("port lock %s held by stale pid %s, taking over", path, prior)
    stream.seek(0)
    stream.truncate()
    stream.write(str(own_pid).encode("ascii"))
    stream.flush()
    return PortLock(path, stream, own_pid)


def _lock_stream(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
