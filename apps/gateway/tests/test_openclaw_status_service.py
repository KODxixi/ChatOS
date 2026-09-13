from __future__ import annotations

import subprocess


def test_openclaw_status_reports_connected_from_sanitized_health() -> None:
    from wechat_ai.services.openclaw_status_service import OpenClawStatusService

    commands: list[list[str]] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok":true,"channels":{"weixin":{"token":"secret"}}}',
            stderr="",
        )

    service = OpenClawStatusService(
        executable_finder=lambda _name: "C:/tools/openclaw.cmd",
        command_runner=run,
    )

    assert service.status() == {
        "installed": True,
        "gatewayReachable": True,
        "status": "connected",
    }
    assert commands == [[
        "C:/tools/openclaw.cmd",
        "health",
        "--json",
        "--timeout",
        "1500",
        "--no-color",
    ]]


def test_openclaw_status_distinguishes_missing_offline_and_invalid_output() -> None:
    from wechat_ai.services.openclaw_status_service import OpenClawStatusService

    missing = OpenClawStatusService(
        executable_finder=lambda _name: None,
        command_runner=lambda command: subprocess.CompletedProcess(command, 0),
    )
    offline = OpenClawStatusService(
        executable_finder=lambda _name: "openclaw",
        command_runner=lambda command: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="gateway unavailable at ws://127.0.0.1:18789?token=secret",
        ),
    )
    invalid = OpenClawStatusService(
        executable_finder=lambda _name: "openclaw",
        command_runner=lambda command: subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok":"yes","token":"secret"}',
            stderr="",
        ),
    )

    assert missing.status() == {
        "installed": False,
        "gatewayReachable": False,
        "status": "not_installed",
    }
    assert offline.status() == {
        "installed": True,
        "gatewayReachable": False,
        "status": "offline",
    }
    assert invalid.status() == {
        "installed": True,
        "gatewayReachable": False,
        "status": "error",
    }
