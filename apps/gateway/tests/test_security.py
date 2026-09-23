from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


SESSION_TOKEN = "synthetic-session-token"
COOKIE_NAME = "wechat_ai_session"


def load_security_module():
    try:
        return importlib.import_module("wechat_ai.security")
    except ModuleNotFoundError as exc:
        raise AssertionError("wechat_ai.security has not been implemented") from exc


def create_secured_app(
    token: str | None = SESSION_TOKEN,
) -> tuple[object, FastAPI, object]:
    security = load_security_module()
    app = FastAPI()

    @app.api_route("/safe", methods=["GET", "HEAD", "OPTIONS"])
    async def safe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/data")
    async def api_data() -> dict[str, bool]:
        return {"ok": True}

    @app.api_route(
        "/api/mutate",
        methods=["POST", "PATCH", "PUT", "DELETE"],
    )
    async def mutate(request: Request) -> dict[str, str]:
        return {"method": request.method}

    installed = security.install_local_security(app, token=token)
    return security, app, installed


def client_for(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:8765",
        "localhost",
        "LOCALHOST:8765",
        "[::1]",
        "[::1]:8765",
    ],
)
def test_allows_only_explicit_loopback_host_forms(host: str) -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        response = client.get("/safe", headers={"host": host})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example",
        "localhost.evil.example",
        "127.0.0.1.evil.example",
        "0.0.0.0:8765",
        "[::1].evil.example",
        "localhost:not-a-port",
        "localhost:70000",
    ],
)
def test_rejects_unexpected_host_with_sanitized_error(host: str) -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        response = client.get("/safe", headers={"host": host})

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_HOST",
            "message": "请求 Host 不受信任。",
            "retryable": False,
            "details": {},
        }
    }
    if host:
        assert host not in response.text


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_first_safe_response_sets_strict_http_only_cookie(method: str) -> None:
    security, app, installed = create_secured_app()

    with client_for(app) as client:
        first = client.request(method, "/safe")
        second = client.request(method, "/safe")

    set_cookie = first.headers["set-cookie"]
    assert installed is None
    assert security.SESSION_COOKIE_NAME == COOKIE_NAME
    assert set_cookie.startswith(f"{COOKIE_NAME}={SESSION_TOKEN};")
    assert "httponly" in set_cookie.casefold()
    assert "samesite=strict" in set_cookie.casefold()
    assert "path=/" in set_cookie.casefold()
    assert "set-cookie" not in second.headers
    assert SESSION_TOKEN not in first.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_unsafe_methods_require_session_cookie(method: str) -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        response = client.request(method, "/api/mutate")

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "SESSION_REQUIRED",
            "message": "需要有效的本地会话。",
            "retryable": False,
            "details": {},
        }
    }
    assert "set-cookie" not in response.headers


def test_wrong_cookie_is_rejected_and_correct_cookie_is_accepted() -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        wrong = client.post(
            "/api/mutate",
            headers={"cookie": f"{COOKIE_NAME}=wrong-token"},
        )
        correct = client.post(
            "/api/mutate",
            headers={"cookie": f"{COOKIE_NAME}={SESSION_TOKEN}"},
        )

    assert wrong.status_code == 403
    assert correct.status_code == 200
    assert correct.json() == {"method": "POST"}
    assert SESSION_TOKEN not in correct.text


def test_unsafe_cookie_comparison_uses_hmac_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security, app, _installed = create_secured_app()
    real_compare_digest = security.hmac.compare_digest
    comparisons: list[tuple[bytes, bytes]] = []

    def record_compare_digest(left: bytes, right: bytes) -> bool:
        comparisons.append((left, right))
        return real_compare_digest(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", record_compare_digest)

    with client_for(app) as client:
        response = client.post(
            "/api/mutate",
            headers={"cookie": f"{COOKIE_NAME}={SESSION_TOKEN}"},
        )

    assert response.status_code == 200
    assert comparisons == [
        (SESSION_TOKEN.encode("utf-8"), SESSION_TOKEN.encode("utf-8"))
    ]


def test_security_headers_apply_to_success_and_error_responses() -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        safe = client.get("/safe")
        api = client.get("/api/data")
        forbidden = client.post("/api/mutate")

    for response in (safe, api, forbidden):
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "base-uri 'none'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'self' http://localhost:* http://127.0.0.1:*" in csp
        assert "evil.example" not in csp
        assert "script-src 'self'" in csp
        assert "'unsafe-eval'" not in csp
        assert "'wasm-unsafe-eval'" not in csp
        assert "img-src 'self' data: blob:" in csp
        assert "connect-src 'self'" in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert "cache-control" not in safe.headers
    assert api.headers["cache-control"] == "no-store"
    assert forbidden.headers["cache-control"] == "no-store"


def test_options_has_no_cors_headers() -> None:
    _security, app, _installed = create_secured_app()

    with client_for(app) as client:
        response = client.options(
            "/safe",
            headers={
                "origin": "https://evil.example",
                "access-control-request-method": "POST",
            },
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_default_generated_token_never_appears_in_body_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _security, app, installed = create_secured_app(token=None)

    with caplog.at_level(logging.DEBUG):
        with client_for(app) as client:
            response = client.get("/safe")

    cookie_value = (
        response.headers["set-cookie"]
        .split(";", 1)[0]
        .split("=", 1)[1]
    )
    assert installed is None
    assert cookie_value
    assert cookie_value not in response.text
    assert cookie_value not in caplog.text


def test_create_app_installs_local_security_by_default(
    tmp_path: Path,
) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "data" / "library.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web-dist",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )

    with TestClient(
        app,
        base_url="http://127.0.0.1",
    ) as trusted:
        first = trusted.get("/api/health")
        accepted = trusted.post("/api/import")
    with TestClient(
        app,
        base_url="http://127.0.0.1",
    ) as without_cookie:
        forbidden = without_cookie.post("/api/import")
    with TestClient(
        app,
        base_url="http://evil.example",
    ) as untrusted:
        bad_host = untrusted.get("/api/health")

    assert first.status_code == 200
    assert COOKIE_NAME in first.cookies
    assert accepted.status_code != 403
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "SESSION_REQUIRED"
    assert bad_host.status_code == 400
    assert bad_host.json()["error"]["code"] == "INVALID_HOST"
