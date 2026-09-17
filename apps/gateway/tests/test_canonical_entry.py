from pathlib import Path
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wechat_ai.plugins.frontend import _mount_frontend
from wechat_ai.security import install_local_security


def test_canonical_redirect_preserves_api_and_session(tmp_path: Path) -> None:
    app = FastAPI()
    install_local_security(app, token="test-session")

    @app.get("/api/example")
    def example():
        return {"real_api": True}

    @app.post("/api/example")
    def write_example():
        return {"authorized": True}

    _mount_frontend(app, tmp_path, frontend_url="http://127.0.0.1:3108")
    with TestClient(app, base_url="http://127.0.0.1:8912", follow_redirects=False) as client:
        assert client.post("/api/example").status_code == 403
        response = client.get("/?next=https://example.com")
        assert response.status_code == 307
        assert response.headers["location"] == "http://127.0.0.1:3108/"
        assert "wechat_ai_session" in response.cookies
        assert client.get("/api/example").json() == {"real_api": True}
        assert client.post("/api/example").json() == {"authorized": True}
        assert client.get("/api/missing").status_code == 404
        assert client.get("/assets/old.js").headers["location"] == "http://127.0.0.1:3108/"


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1:3108@evil.test", "//evil.test"])
def test_redirect_rejects_external_configuration(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        _mount_frontend(FastAPI(), tmp_path, frontend_url=url)


def test_gateway_startup_identity_is_fixed_and_content_based(tmp_path: Path) -> None:
    from wechat_ai import runtime_contract

    assert hasattr(runtime_contract, "gateway_source_identity"), "gateway needs code startup identity"
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "example.py"
    source.write_text("version = 1", encoding="utf-8")
    first = runtime_contract.gateway_source_identity(tmp_path)
    assert first == runtime_contract.gateway_source_identity(tmp_path)
    source.write_text("version = 2", encoding="utf-8")
    assert first != runtime_contract.gateway_source_identity(tmp_path)
    assert len(runtime_contract.GATEWAY_STARTUP_IDENTITY) == 64


def test_config_identity_captures_loaded_bytes(tmp_path: Path) -> None:
    from wechat_ai.config import load_settings

    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'frontendUrl': 'http://127.0.0.1:3108'}), encoding='utf-8')
    settings = load_settings(project_root=tmp_path, settings_path=config)
    assert hasattr(settings, 'config_identity'), 'loaded settings need a fixed identity'
    before = settings.config_identity
    config.write_text('{}', encoding='utf-8')
    assert settings.config_identity == before
    assert load_settings(project_root=tmp_path, settings_path=config).config_identity != before
