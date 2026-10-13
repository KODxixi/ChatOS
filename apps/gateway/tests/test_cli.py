from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from typer.testing import CliRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _isolate_settings(monkeypatch, tmp_path: Path) -> None:
    from wechat_ai import config

    monkeypatch.setattr(
        config,
        "load_settings",
        lambda: config.Settings(
            project_root=tmp_path,
            source_dir=tmp_path / "decrypted",
            library_db=tmp_path / "library.sqlite",
            output_dir=tmp_path / "output",
            web_dist=tmp_path / "web",
            lm_base_url="http://127.0.0.1:9123/v1",
        ),
    )


def test_project_exposes_python_313_chatbox_release_contract() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))

    assert project["project"]["requires-python"] == ">=3.13"
    assert project["project"]["name"] == "chatbox"
    assert project["project"]["version"] == "1.5.0"
    assert project["project"]["scripts"]["chatbox"] == "wechat_ai.cli:cli"
    assert project["project"]["scripts"]["gchat"] == "wechat_ai.cli:cli"
    assert project["project"]["scripts"]["wechat-ai"] == "wechat_ai.cli:cli"
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/wechat_ai"
    ]


def test_serve_binds_uvicorn_to_loopback_and_honors_options(monkeypatch, tmp_path: Path) -> None:
    from wechat_ai import cli as cli_module

    _isolate_settings(monkeypatch, tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    fake_app = object()
    monkeypatch.setattr(cli_module, "create_app", lambda _settings: fake_app)
    monkeypatch.setattr(cli_module, "_existing_runtime_status", lambda _url: "absent")
    monkeypatch.setattr(cli_module.uvicorn, "run", fake_run)
    runner = CliRunner()

    result = runner.invoke(
        cli_module.cli,
        ["serve", "--port", "9012", "--no-browser"],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["app"] is fake_app
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9012


def test_serve_schedules_browser_for_ready_app_by_default(monkeypatch, tmp_path: Path) -> None:
    from wechat_ai import cli as cli_module

    _isolate_settings(monkeypatch, tmp_path)
    opened_urls: list[str] = []
    monkeypatch.setattr(cli_module, "create_app", lambda _settings: object())
    monkeypatch.setattr(cli_module, "_existing_runtime_status", lambda _url: "absent")
    monkeypatch.setattr(cli_module.uvicorn, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "_start_browser_when_ready",
        opened_urls.append,
    )

    result = CliRunner().invoke(cli_module.cli, ["serve", "--port", "9013"])

    assert result.exit_code == 0, result.output
    assert opened_urls == ["http://127.0.0.1:9013"]


def test_serve_refuses_to_open_an_incompatible_existing_service(monkeypatch) -> None:
    from wechat_ai import cli as cli_module

    create_app = monkeypatch.setattr(
        cli_module,
        "create_app",
        lambda: (_ for _ in ()).throw(AssertionError("must not create app")),
    )
    del create_app
    monkeypatch.setattr(
        cli_module,
        "_existing_runtime_status",
        lambda _url: "incompatible",
    )

    result = CliRunner().invoke(
        cli_module.cli,
        ["serve", "--port", "9014", "--no-browser"],
    )

    assert result.exit_code != 0
    assert "旧版本" in result.output
    assert "重启" in result.output


def test_serve_reuses_a_compatible_existing_service(monkeypatch) -> None:
    from wechat_ai import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "create_app",
        lambda: (_ for _ in ()).throw(AssertionError("must not create app")),
    )
    monkeypatch.setattr(
        cli_module,
        "_existing_runtime_status",
        lambda _url: "compatible",
    )
    opened_urls: list[str] = []
    monkeypatch.setattr(cli_module.webbrowser, "open", opened_urls.append)

    result = CliRunner().invoke(cli_module.cli, ["serve", "--port", "9015"])

    assert result.exit_code == 0, result.output
    assert opened_urls == ["http://127.0.0.1:9015"]


def test_load_settings_resolves_relative_paths_from_project_root(
    tmp_path: Path,
) -> None:
    from wechat_ai.config import load_settings

    settings_path = tmp_path / "config" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "sourceDir": "decrypted",
                "libraryDb": "data/library/wechat-ai.sqlite",
                "outputDir": "output",
                "lmStudio": {"baseUrl": "http://127.0.0.1:1234/v1"},
            }
        ),
        encoding="utf-8",
    )
    source_database_root = tmp_path / "wechat-account" / "db_storage"
    source_database_root.mkdir(parents=True)
    (tmp_path / "config.json").write_text(
        json.dumps({"db_dir": str(source_database_root)}),
        encoding="utf-8",
    )

    settings = load_settings(
        project_root=tmp_path,
        settings_path=settings_path,
    )

    assert settings.source_dir == tmp_path / "decrypted"
    assert settings.library_db == tmp_path / "data" / "library" / "wechat-ai.sqlite"
    assert settings.asset_dir == tmp_path / "data" / "library" / "assets"
    assert settings.cache_dir == tmp_path / "data" / "cache"
    assert settings.backup_dir == tmp_path / "data" / "backups"
    assert settings.output_dir == tmp_path / "output"
    assert settings.lm_base_url == "http://127.0.0.1:1234/v1"
    assert (
        settings.wechat_media_root
        == tmp_path / "wechat-account" / "msg" / "attach"
    )


def test_serve_rejects_when_single_instance_lock_is_held(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from wechat_ai import cli as cli_module
    from wechat_ai import runtime_singleton as singleton

    _isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli_module,
        "_existing_runtime_status",
        lambda _url: "absent",
    )

    def boom(_path, **_kwargs):
        raise singleton.PortLockHeld(
            owner_pid=12345,
            path=tmp_path / "wechat_ai-9016.pid",
        )

    monkeypatch.setattr(singleton, "acquire_port_lock", boom)
    monkeypatch.setattr(
        cli_module,
        "create_app",
        lambda _s: (_ for _ in ()).throw(AssertionError("must not create app")),
    )
    monkeypatch.setattr(
        cli_module.uvicorn,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = CliRunner().invoke(
        cli_module.cli,
        ["serve", "--port", "9016", "--no-browser"],
    )

    assert result.exit_code != 0
    assert "拒绝重复启动" in result.output


def test_load_settings_configures_project_whisper_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from wechat_ai.config import load_settings

    whisper_cpp = (
        tmp_path / "tools" / "whisper.cpp" / "bin" / "Release" / "whisper-cli.exe"
    )
    whisper_model = tmp_path / "tools" / "whisper.cpp" / "models" / "ggml-base.bin"
    whisper_cpp.parent.mkdir(parents=True)
    whisper_model.parent.mkdir(parents=True)
    whisper_cpp.write_bytes(b"binary")
    whisper_model.write_bytes(b"model")
    monkeypatch.delenv("CHATBOX_WHISPER_CPP", raising=False)
    monkeypatch.delenv("CHATBOX_WHISPER_MODEL", raising=False)

    load_settings(project_root=tmp_path)

    assert Path(os.environ["CHATBOX_WHISPER_CPP"]) == whisper_cpp
    assert Path(os.environ["CHATBOX_WHISPER_MODEL"]) == whisper_model
