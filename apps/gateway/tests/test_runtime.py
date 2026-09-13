from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_runtime_reports_checked_out_contract_and_schema(tmp_path: Path) -> None:
    from wechat_ai.app import create_app
    from wechat_ai.config import Settings

    web_dist = tmp_path / "web" / "dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text(
        "<html><body>runtime-build</body></html>",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "missing-source",
        library_db=tmp_path / "data" / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        web_dist=web_dist,
        lm_base_url="http://127.0.0.1:1234/v1",
    )

    response = TestClient(
        create_app(
            settings,
            source_probe=lambda: "missing",
            lm_probe=lambda: "offline",
        ),
        base_url="http://127.0.0.1",
    ).get("/api/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "appVersion": "1.5.0",
        "apiContractVersion": "2026-08-05",
        "schemaVersion": 13,
        "requiredSchemaVersion": 13,
        "webBuildId": payload["webBuildId"],
    }
    assert len(payload["webBuildId"]) == 64
    assert set(payload["webBuildId"]) <= set("0123456789abcdef")
