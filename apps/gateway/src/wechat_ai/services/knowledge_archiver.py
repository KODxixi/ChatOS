"""Build the disposable ChatOS file/archive projection from read-only WeChat data."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge_service import safe_knowledge_text

logger = logging.getLogger(__name__)

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_KINDS = ("attachment", "image", "audio", "video", "link")


def _safe_name(name: str, fallback: str) -> str:
    cleaned = _INVALID_FS.sub("_", (name or "").strip()).strip(". ")
    return cleaned or fallback


def _content_hash(path: Path, stored_name: str) -> str:
    if _CONTENT_HASH.fullmatch(stored_name.casefold()):
        return stored_name.casefold()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _link_or_copy(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return True


def _write_registry(knowledge_root: Path, *, retrieval_base_url: str = "http://127.0.0.1:9123/v1") -> None:
    # Existing registries describe the active index; archive is not a migration.
    target = knowledge_root / "local-model-registry.yaml"
    if target.exists():
        return
    from ..config import DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMENSIONS
    from .reranker_service import _validated_loopback_url
    endpoint = json.dumps(_validated_loopback_url(retrieval_base_url).removesuffix("/v1"))
    index_root = str((knowledge_root / "index").resolve())
    registry = (
        "version: 3\n"
        "text_backend:\n"
        f"  base_url: {endpoint}\n"
        "  embedding:\n"
        f"    base_url: {endpoint}\n"
        f"    model: {json.dumps(DEFAULT_EMBEDDING_MODEL)}\n"
        f"    dimensions: {DEFAULT_EMBEDDING_DIMENSIONS}\n"
        "  rerank:\n"
        f"    base_url: {endpoint}\n"
        "    model: vl-reranker-2b\n"
        "vector_store:\n"
        "  type: lancedb\n"
        "knowledge_bases:\n"
        "  chatos:\n"
        "    type: text\n"
        f"    root: {json.dumps(index_root, ensure_ascii=False)}\n"
        "    patterns: [\"*.md\"]\n"
        f"    dimensions: {DEFAULT_EMBEDDING_DIMENSIONS}\n"
        f"    embed_model: {json.dumps(DEFAULT_EMBEDDING_MODEL)}\n"
        "    max_file_bytes: 5000000\n"
        "    max_files: 100000\n"
        "    embed_batch_size: 64\n"
        "    warmup_on_index: true\n"
    )
    (knowledge_root / "local-model-registry.yaml").write_text(registry, encoding="utf-8")


def archive_library(
    connection_factory,
    *,
    library_root: Path,
    asset_root: Path | None,
    retrieval_base_url: str = "http://127.0.0.1:9123/v1",
) -> dict[str, object]:
    """Create a rebuildable archive + Markdown index without mutating source rows."""
    knowledge_root = library_root / "knowledge"
    index_root = knowledge_root / "index"
    catalog_root = knowledge_root / "catalog"
    objects_root = knowledge_root / "archive" / "objects"
    index_root.mkdir(parents=True, exist_ok=True)
    catalog_root.mkdir(parents=True, exist_ok=True)
    objects_root.mkdir(parents=True, exist_ok=True)

    stats: dict[str, object] = {
        "assetsArchived": 0,
        "assetsUnchanged": 0,
        "unresolvedAssets": 0,
        "documentsWritten": 0,
        "catalogDocumentsWritten": 0,
        "partsRecorded": 0,
        "errors": 0,
    }

    with closing(connection_factory()) as connection:
        connection.row_factory = sqlite3.Row
        assets_by_part: dict[int, list[dict[str, Any]]] = defaultdict(list)
        asset_root_resolved = asset_root.resolve() if asset_root is not None else None
        for row in connection.execute(
            """
            SELECT id, part_id, kind, status, file_name, mime_type,
                   byte_size, external_url, local_path
            FROM assets
            WHERE kind IN ('attachment', 'image', 'audio', 'video')
            ORDER BY part_id, ordinal, id
            """
        ):
            asset = dict(row)
            asset["archive_path"] = None
            local_path = str(row["local_path"] or "")
            if row["status"] != "resolved" or not local_path or asset_root_resolved is None:
                stats["unresolvedAssets"] = int(stats["unresolvedAssets"]) + 1
            else:
                source = (asset_root_resolved / local_path).resolve()
                try:
                    source.relative_to(asset_root_resolved)
                    if not source.is_file():
                        raise FileNotFoundError(source)
                    digest = _content_hash(source, Path(local_path).name)
                    destination = objects_root / digest[:2] / digest
                    created = _link_or_copy(source, destination)
                    key = "assetsArchived" if created else "assetsUnchanged"
                    stats[key] = int(stats[key]) + 1
                    asset["archive_path"] = destination.relative_to(knowledge_root).as_posix()
                except (OSError, ValueError) as exc:
                    logger.warning("knowledge asset archive failed %s: %s", source, exc)
                    stats["errors"] = int(stats["errors"]) + 1
            assets_by_part[int(row["part_id"])].append(asset)

        placeholders = ", ".join("?" for _kind in _SUPPORTED_KINDS)
        rows = connection.execute(
            f"""
            SELECT mp.id AS part_id, mp.kind, mp.status, mp.text_content,
                   m.id AS message_id, m.conversation_id, m.sender_name,
                   m.sent_at, m.content, c.title AS conversation_title,
                   GROUP_CONCAT(rc.text_content, '\n\n') AS extracted_text
            FROM message_parts mp
            JOIN messages m ON m.id = mp.message_id
            JOIN conversations c ON c.id = m.conversation_id
            LEFT JOIN retrieval_chunks rc ON rc.message_part_id = mp.id
            WHERE mp.kind IN ({placeholders})
            GROUP BY mp.id
            ORDER BY m.conversation_id, m.sent_at, mp.id
            """,
            _SUPPORTED_KINDS,
        ).fetchall()

        documents: dict[tuple[str, str], list[str]] = defaultdict(list)
        catalog_documents: dict[tuple[str, str], list[str]] = defaultdict(list)
        titles: dict[tuple[str, str], str] = {}
        for row in rows:
            month = datetime.fromtimestamp(int(row["sent_at"]) / 1000, tz=timezone.utc).strftime("%Y-%m")
            key = (str(row["conversation_id"]), month)
            titles[key] = str(row["conversation_title"] or row["conversation_id"])
            part_assets = assets_by_part.get(int(row["part_id"]), [])
            raw_content = str(row["extracted_text"] or row["text_content"] or "").strip()
            if not raw_content and row["kind"] == "link":
                raw_content = str(row["content"] or "").strip()
            if not raw_content and row["kind"] == "attachment":
                raw_content = " ".join(
                    str(asset["file_name"])
                    for asset in part_assets
                    if asset["status"] == "resolved" and asset["file_name"]
                )
            catalog_content = safe_knowledge_text(str(row["kind"]), raw_content)
            index_content = safe_knowledge_text(
                str(row["kind"]), raw_content, for_index=True
            )
            lines = [
                f"## {row['kind']} · {row['part_id']}",
                "",
                f"- source_conversation_id: {row['conversation_id']}",
                f"- source_message_id: {row['message_id']}",
                f"- source_part_id: {row['part_id']}",
                f"- sender: {row['sender_name']}",
                f"- sent_at: {row['sent_at']}",
                f"- kind: {row['kind']}",
                f"- status: {row['status']}",
            ]
            for asset in part_assets:
                lines.extend(
                    [
                        f"- asset_id: {asset['id']}",
                        f"- asset_name: {asset['file_name'] or '未命名'}",
                        f"- asset_type: {asset['mime_type'] or asset['kind']}",
                        f"- asset_status: {asset['status']}",
                    ]
                )
                if asset["archive_path"]:
                    lines.append(f"- archive_object: {asset['archive_path']}")
            catalog_lines = [*lines]
            if catalog_content:
                catalog_lines.extend(["", "### 内容状态", "", catalog_content])
            catalog_documents[key].append("\n".join(catalog_lines))
            if index_content and (row["status"] == "resolved" or row["kind"] == "link"):
                documents[key].append(
                    "\n".join([*lines, "", "### 可检索内容", "", index_content])
                )
            stats["partsRecorded"] = int(stats["partsRecorded"]) + 1

        def write_documents(root: Path, grouped: dict[tuple[str, str], list[str]], stat: str) -> None:
            for (conversation_id, month), sections in grouped.items():
                destination = root / _safe_name(conversation_id, "conversation") / f"{month}.md"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    f"# {titles[(conversation_id, month)]} · {month}\n\n"
                    "> ChatOS 可重建知识投影；证据定位以 source_* 字段为准。\n\n"
                    + "\n\n---\n\n".join(sections)
                    + "\n",
                    encoding="utf-8",
                )
                stats[stat] = int(stats[stat]) + 1

        write_documents(catalog_root, catalog_documents, "catalogDocumentsWritten")
        write_documents(index_root, documents, "documentsWritten")

    _write_registry(knowledge_root, retrieval_base_url=retrieval_base_url)
    (knowledge_root / "README.md").write_text(
        "# ChatOS 本地知识库\n\n"
        "微信数据库与原始媒体是只读真源；本目录全部内容均为可删除重建的投影。\n\n"
        "- `archive/objects/`：已解析媒体的内容寻址硬链接（跨盘时复制）\n"
        "- `catalog/`：含未解析状态在内的完整证据清单，不进入 embedding\n"
        "- `index/`：仅含可检索正文，供 local-model 建立向量索引\n"
        "- `lancedb/`：local-model 的 ChatOS 独立向量表\n",
        encoding="utf-8",
    )
    logger.info("knowledge archive: %s", stats)
    return stats
