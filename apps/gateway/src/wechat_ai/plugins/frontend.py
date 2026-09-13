"""Frontend plugin: mounts the compiled WebUI (or a placeholder)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

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
    ctx.add_catchall(lambda app: _mount_frontend(app, dist))


def _mount_frontend(app: FastAPI, web_dist: Path) -> None:
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
