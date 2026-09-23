from __future__ import annotations

from collections import Counter
from pathlib import Path

from wechat_ai.app import create_app
from wechat_ai.config import Settings


def test_project_routes_have_one_owner_per_method(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        web_dist=tmp_path / "missing-web",
        lm_base_url="http://127.0.0.1:9123/v1",
    )
    app = create_app(
        settings,
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    routes = Counter(
        (method, owner.include_context.prefix + route.path)
        for owner in app.routes
        if hasattr(owner, "original_router")
        for route in owner.original_router.routes
        for method in getattr(route, "methods", ())
        if method in {"GET", "POST"}
    )
    assert routes["GET", "/api/projects"] == 1
    assert routes["POST", "/api/projects"] == 1
    assert routes["GET", "/api/conversations/projects"] == 1
