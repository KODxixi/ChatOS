from __future__ import annotations

import json
import os
import socket
import time
import webbrowser
from pathlib import Path
from typing import BinaryIO

import uvicorn

from .app import create_app
from .cli import LOOPBACK_HOST, _existing_runtime_status, _start_browser_when_ready
from .config import Settings, load_settings
from .runtime_contract import APP_VERSION


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        if self._stream is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stream = self._path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            _lock_stream(stream)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        stream.seek(0)
        try:
            _unlock_stream(stream)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError("Chatbox is already running")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((LOOPBACK_HOST, 0))
        return int(listener.getsockname()[1])


def main() -> int:
    settings = load_settings()
    state_dir = settings.project_root / "data" / "state"
    state_path = state_dir / "runtime.json"
    instance = SingleInstanceLock(state_dir / "gchat.lock")
    if not instance.acquire():
        existing_url = _running_instance_url(state_path)
        if existing_url is None:
            return 1
        webbrowser.open(existing_url)
        return 0

    try:
        port = find_available_port()
        base_url = f"http://{LOOPBACK_HOST}:{port}"
        _write_runtime_state(state_path, port)
        app = create_app(settings)
        _start_browser_when_ready(base_url)
        uvicorn.run(app, host=LOOPBACK_HOST, port=port)
        return 0
    finally:
        _remove_owned_runtime_state(state_path)
        instance.release()


def _running_instance_url(state_path: Path) -> str | None:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    port = payload.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return None
    base_url = f"http://{LOOPBACK_HOST}:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _existing_runtime_status(base_url) == "compatible":
            return base_url
        time.sleep(0.1)
    return None


def _write_runtime_state(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {"port": port, "appVersion": APP_VERSION},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _remove_owned_runtime_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


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
