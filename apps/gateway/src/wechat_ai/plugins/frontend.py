"""Frontend plugin: mounts the compiled WebUI (or a placeholder)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

_PLACEHOLDER_HTML = """\
<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>Chatbox</title></head>
  <body>
    <main>
      <h1>Chatbox</h1>
      <p>WebUI 尚未构建。请先生成 web/dist，再刷新此页面。</p>
    </main>
  </body>
</html>
"""


def apply(ctx) -> None:
    settings = ctx.get("settings")
    dist = (ctx.override("web_dist") or settings.web_dist).resolve()
    ctx.add_catchall(lambda app: _mount_frontend(app, dist, frontend_url=settings.frontend_url))


def _mount_frontend(app: FastAPI, web_dist: Path, *, frontend_url: str | None = None) -> None:
    if frontend_url is not None:
        target = urlsplit(frontend_url)
        if (target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or target.username or target.password or target.path not in {"", "/"}
                or target.query or target.fragment):
            raise ValueError("frontendUrl must be a loopback HTTP origin")
        # Validate the port too; never accept a request-supplied redirect target/path.
        _ = target.port
        destination = frontend_url.rstrip("/") + "/"

        @app.get("/{full_path:path}", include_in_schema=False)
        def canonical_frontend(full_path: str) -> RedirectResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            return RedirectResponse(destination, status_code=307)

        return

    index = web_dist / "index.html"
    if not index.is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        def placeholder(full_path: str) -> HTMLResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            return HTMLResponse(_PLACEHOLDER_HTML)

        return

    dist_root = web_dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        candidate = (dist_root / full_path).resolve()
        try:
            candidate.relative_to(dist_root)
        except ValueError:
            candidate = index
        if not candidate.is_file():
            candidate = index
        return FileResponse(candidate)
