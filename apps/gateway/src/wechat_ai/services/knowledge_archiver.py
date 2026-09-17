"""Knowledge archiver (ChatOS v0.3.0).

Copies local file attachments into a tidy library tree and dumps extracted
links per conversation as Markdown, so the vault becomes a browsable
knowledge base independent of the WeChat import tables.

Layout (root = <library_db_dir>, i.e. D:\\ChatOS-Vault\\library):

    files/<conversationTitle>/<yyyy-MM>/<fileName>
    links/<conversationTitle>.md
    README.md
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fa5，。！、）)】\]\"'<>]+")
_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str, fallback: str) -> str:
    cleaned = _INVALID_FS.sub("_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    return cleaned or fallback


def archive_library(
    connection_factory,
    *,
    library_root: Path,
    asset_root: Path | None,
) -> dict[str, object]:
    """Run the archive pass. Returns a stats dict."""
    files_dir = library_root / "files"
    links_dir = library_root / "links"
    files_dir.mkdir(parents=True, exist_ok=True)
    links_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, object] = {
        "filesCopied": 0,
        "filesSkipped": 0,
        "linksWritten": 0,
        "conversations": 0,
        "errors": 0,
    }

    with closing(connection_factory()) as connection:
        connection.row_factory = sqlite3.Row

        # ---- files: attachment messages with a resolved local asset -------
        if asset_root is not None:
            asset_root_resolved = asset_root.resolve()
            file_rows = connection.execute(
                """
                SELECT m.id, m.conversation_id, m.sent_at,
                       c.title AS conversation_title,
                       a.file_name, a.local_path, a.status AS asset_status
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                LEFT JOIN message_parts mp ON mp.message_id = m.id
                LEFT JOIN assets a ON a.part_id = mp.id
                WHERE m.type = 'attachment'
                  AND a.local_path IS NOT NULL AND a.local_path != ''
                """
            ).fetchall()

            for r in file_rows:
                if r["asset_status"] != "resolved":
                    stats["filesSkipped"] = stats["filesSkipped"] + 1
                    continue
                src = (asset_root_resolved / r["local_path"]).resolve()
                try:
                    src.relative_to(asset_root_resolved)
                except ValueError:
                    stats["filesSkipped"] = stats["filesSkipped"] + 1
                    continue
                if not src.is_file():
                    stats["filesSkipped"] = stats["filesSkipped"] + 1
                    continue
                title = _safe_name(r["conversation_title"], r["conversation_id"])
                month = datetime.fromtimestamp(
                    r["sent_at"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m")
                fname = _safe_name(r["file_name"], f"attachment_{r['id']}")
                dest_dir = files_dir / title / month
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / fname
                if dest.exists():
                    stats["filesSkipped"] = stats["filesSkipped"] + 1
                    continue
                try:
                    shutil.copy2(src, dest)
                    stats["filesCopied"] = stats["filesCopied"] + 1
                except OSError as exc:
                    logger.warning("archive copy failed %s: %s", src, exc)
                    stats["errors"] = stats["errors"] + 1

        # ---- links: one Markdown file per conversation --------------------
        link_rows = connection.execute(
            """
            SELECT m.conversation_id, c.title AS conversation_title,
                   m.sender_name, m.sent_at, m.content
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.type = 'text' AND m.content LIKE '%http%'
            ORDER BY m.sent_at
            """
        ).fetchall()

        by_conv: dict[str, list[dict[str, object]]] = {}
        titles: dict[str, str] = {}
        for r in link_rows:
            urls = _URL_RE.findall(r["content"] or "")
            if not urls:
                continue
            by_conv.setdefault(r["conversation_id"], []).append({
                "sent_at": r["sent_at"],
                "sender": r["sender_name"],
                "urls": urls,
            })
            titles[r["conversation_id"]] = r["conversation_title"] or r["conversation_id"]

        for conv_id, entries in by_conv.items():
            title = _safe_name(titles[conv_id], conv_id)
            lines = [f"# 链接归档 · {titles[conv_id]}", ""]
            current_date = ""
            for e in entries:
                day = datetime.fromtimestamp(
                    e["sent_at"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
                if day != current_date:
                    current_date = day
                    lines += ["", f"## {day}", ""]
                for url in e["urls"]:
                    lines.append(f"- {e['sender']}: {url}")
            md_path = links_dir / f"{title}.md"
            md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            stats["linksWritten"] = stats["linksWritten"] + 1
            stats["conversations"] = stats["conversations"] + 1

        # ---- README ------------------------------------------------------
        readme = library_root / "README.md"
        readme.write_text(
            "# ChatOS 知识库归档\n\n"
            "由 `POST /api/knowledge/archive` 自动生成。\n\n"
            "- `files/` — 按会话/月份归档的本地附件\n"
            "- `links/` — 每个会话一个 Markdown，按日期列出提取到的链接\n",
            encoding="utf-8",
        )

    logger.info("knowledge archive: %s", stats)
    return stats
