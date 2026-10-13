from __future__ import annotations

import os
import subprocess
import sys


def _dead_pid() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os; os._exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait()
    return proc.pid


def test_process_is_alive_distinguishes_live_and_dead() -> None:
    from wechat_ai.runtime_singleton import process_is_alive

    assert process_is_alive(os.getpid()) is True
    assert process_is_alive(_dead_pid()) is False
    assert process_is_alive(0) is False


def test_port_lock_absent_acquire_held_rejects_and_stale_takeover(tmp_path) -> None:
    from wechat_ai.runtime_singleton import PortLockHeld, acquire_port_lock

    path = tmp_path / "var" / "run" / "wechat_ai-9999.pid"

    # absent -> acquire
    lock = acquire_port_lock(path, pid=111111)
    try:
        assert path.exists()
        # held by a live holder -> second acquire rejected
        try:
            acquire_port_lock(path, pid=222222)
            raise AssertionError("expected PortLockHeld when lock is held")
        except PortLockHeld:
            pass
    finally:
        lock.release()
    # clean release removes the marker
    assert not path.exists()

    # stale: a crashed process left a pid file (OS lock already released by the OS),
    # owner pid is dead -> a fresh instance takes over.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(_dead_pid()), encoding="utf-8")
    taken = acquire_port_lock(path, pid=333333)
    taken.release()


def test_port_lock_does_not_double_start_a_live_owner(tmp_path) -> None:
    from wechat_ai.runtime_singleton import PortLockHeld, acquire_port_lock

    path = tmp_path / "wechat_ai-8080.pid"
    lock = acquire_port_lock(path, pid=111111)
    try:
        try:
            acquire_port_lock(path, pid=444444)
            raise AssertionError("expected PortLockHeld")
        except PortLockHeld:
            pass
    finally:
        lock.release()
