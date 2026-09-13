from __future__ import annotations

import socket
from pathlib import Path


def test_random_loopback_port_is_available() -> None:
    from wechat_ai.launcher import find_available_port

    port = find_available_port()

    assert 1 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


def test_windows_file_lock_enforces_single_instance(tmp_path: Path) -> None:
    from wechat_ai.launcher import SingleInstanceLock

    first = SingleInstanceLock(tmp_path / "gchat.lock")
    second = SingleInstanceLock(tmp_path / "gchat.lock")

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_launcher_reuses_running_instance_without_starting_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from wechat_ai import launcher
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "data" / "library" / "gchat.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "web" / "dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    opened: list[str] = []
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(
        launcher.SingleInstanceLock,
        "acquire",
        lambda _self: False,
    )
    monkeypatch.setattr(launcher, "_running_instance_url", lambda _state: "http://127.0.0.1:54321")
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("server must not start")
        ),
    )

    assert launcher.main() == 0
    assert opened == ["http://127.0.0.1:54321"]
