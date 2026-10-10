from __future__ import annotations

import json
import threading
import time
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer
import uvicorn

from .app import create_app
from .runtime_contract import is_compatible_runtime_payload


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8912
cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.callback()
def main() -> None:
    """Chatbox local data-pool and knowledge workspace command line interface."""


@cli.command()
def serve(
    port: int = typer.Option(
        DEFAULT_PORT,
        "--port",
        min=1,
        max=65535,
        help="Local loopback port.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Do not open the WebUI in the default browser.",
    ),
) -> None:
    """Start the local Chatbox web application."""

    base_url = f"http://{LOOPBACK_HOST}:{port}"
    existing_status = _existing_runtime_status(base_url)
    if existing_status == "incompatible":
        typer.echo(
            "检测到该端口运行的是旧版本或不兼容的本地服务。"
            "请先重启/升级该服务，再启动当前版本。",
            err=True,
        )
        raise typer.Exit(code=1)
    if existing_status == "compatible":
        if not no_browser:
            webbrowser.open(base_url)
        return

    from .config import load_settings as _load_settings
    from .services.embedding_service import configure_from_settings as _cfg_embed
    from .services.message_cleaning import configure as _cfg_clean
    _s = _load_settings()
    _cfg_embed(_s)
    _cfg_clean(_s)
    app = create_app(_s)
    if not no_browser:
        _start_browser_when_ready(base_url)
    uvicorn.run(app, host=LOOPBACK_HOST, port=port)


def _existing_runtime_status(base_url: str) -> str:
    request = Request(
        f"{base_url}/api/runtime",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=0.5) as response:
            payload = json.load(response)
    except HTTPError:
        return "incompatible"
    except (OSError, TimeoutError, URLError):
        return "absent"
    except json.JSONDecodeError:
        return "incompatible"
    if response.status != 200:
        return "incompatible"
    return "compatible" if is_compatible_runtime_payload(payload) else "incompatible"


def _start_browser_when_ready(base_url: str) -> None:
    thread = threading.Thread(
        target=_open_browser_when_ready,
        args=(base_url,),
        name="wechat-ai-browser",
        daemon=True,
    )
    thread.start()


def _open_browser_when_ready(base_url: str) -> None:
    deadline = time.monotonic() + 30
    health_url = f"{base_url}/api/health"
    while time.monotonic() < deadline:
        if _app_is_ready(health_url):
            webbrowser.open(base_url)
            return
        time.sleep(0.1)


def _app_is_ready(health_url: str) -> bool:
    request = Request(health_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=0.5) as response:
            payload = json.load(response)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError):
        return False
    return response.status == 200 and payload.get("app") == "ready"
