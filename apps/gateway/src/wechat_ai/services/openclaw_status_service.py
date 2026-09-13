from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from typing import Any


ExecutableFinder = Callable[[str], str | None]
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class OpenClawStatusService:
    """Read only the minimum local OpenClaw health needed by Chatbox's UI."""

    def __init__(
        self,
        *,
        executable_finder: ExecutableFinder | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._executable_finder = executable_finder or shutil.which
        self._command_runner = command_runner or _run_health_command

    def status(self) -> dict[str, object]:
        executable = self._executable_finder("openclaw")
        if executable is None:
            return _public_status(
                installed=False,
                gateway_reachable=False,
                status="not_installed",
            )

        command = [
            executable,
            "health",
            "--json",
            "--timeout",
            "1500",
            "--no-color",
        ]
        try:
            completed = self._command_runner(command)
        except (OSError, subprocess.SubprocessError):
            return _public_status(
                installed=True,
                gateway_reachable=False,
                status="offline",
            )

        if completed.returncode != 0:
            return _public_status(
                installed=True,
                gateway_reachable=False,
                status="offline",
            )

        try:
            payload: Any = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            return _public_status(
                installed=True,
                gateway_reachable=False,
                status="error",
            )
        reachable = payload["ok"] is True
        return _public_status(
            installed=True,
            gateway_reachable=reachable,
            status="connected" if reachable else "offline",
        )


def _run_health_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=4,
        check=False,
        creationflags=creation_flags,
    )


def _public_status(
    *,
    installed: bool,
    gateway_reachable: bool,
    status: str,
) -> dict[str, object]:
    return {
        "installed": installed,
        "gatewayReachable": gateway_reachable,
        "status": status,
    }
