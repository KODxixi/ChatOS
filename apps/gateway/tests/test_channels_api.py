from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeOpenClawStatusService:
    def status(self) -> dict[str, object]:
        return {
            "installed": True,
            "gatewayReachable": True,
            "status": "connected",
        }


def test_openclaw_status_is_read_only_and_sanitized() -> None:
    from wechat_ai.api.channels import create_channels_router

    app = FastAPI()
    app.include_router(
        create_channels_router(FakeOpenClawStatusService()),
        prefix="/api",
    )
    client = TestClient(app)

    response = client.get("/api/channels/openclaw/status")

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "gatewayReachable": True,
        "status": "connected",
    }
    serialized = response.text.casefold()
    for forbidden in ("token", "credential", "endpoint", "url", "session"):
        assert forbidden not in serialized


def test_embedded_weixin_login_routes_are_removed() -> None:
    from wechat_ai.api.channels import create_channels_router

    app = FastAPI()
    app.include_router(
        create_channels_router(FakeOpenClawStatusService()),
        prefix="/api",
    )
    client = TestClient(app)

    assert client.post("/api/channels/weixin/login/start").status_code == 404
    assert client.get("/api/channels/weixin/login/login-1").status_code == 404
    assert client.get("/api/channels/weixin/status").status_code == 404
    assert client.post("/api/channels/weixin/disconnect", json={}).status_code == 404
