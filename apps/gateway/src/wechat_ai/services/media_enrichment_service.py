from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import base64
import json
import urllib.request

from ..wechat_media import (
    decode_silk_to_wav,
    prepare_silk_wav,
    record_playback_state,
)

ImageTextExtractor = Callable[[Path], str | None]
AudioTranscriber = Callable[[Path], str | None]
SilkDecoder = Callable[[bytes], bytes]


@dataclass(frozen=True, slots=True)
class MediaEnrichmentResult:
    ready: int = 0
    skipped: int = 0
    errors: int = 0
    attempted: int = 0
    no_text: int = 0
    engine_unavailable: int = 0
    has_more: bool = False


class MediaEnrichmentService:
    """Optional local OCR/ASR projection; never changes source databases."""

    def __init__(
        self,
        *,
        asset_root: Path,
        cache_root: Path | None = None,
        image_text_extractor: ImageTextExtractor | None = None,
        audio_transcriber: AudioTranscriber | None = None,
        silk_decoder: SilkDecoder = decode_silk_to_wav,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._asset_root = asset_root.resolve()
        self._cache_root = cache_root.resolve() if cache_root is not None else None
        if image_text_extractor is not None:
            self._image_text_extractor = image_text_extractor
            self._image_extractor_available = True
        elif _vlm_available():
            self._image_text_extractor = _vlm_image_extractor
            self._image_extractor_available = True
        else:
            self._image_text_extractor = _rapidocr_extractor
            self._image_extractor_available = _rapidocr_available()
        self._audio_transcriber = audio_transcriber or _whisper_cpp_transcriber
        self._audio_transcriber_available = (
            audio_transcriber is not None or _whisper_cpp_available()
        )
        self._silk_decoder = silk_decoder
        self._clock = clock or (lambda: int(time.time() * 1000))

    def enrich_pending(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int = 50,
    ) -> MediaEnrichmentResult:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT assets.id, assets.kind, assets.mime_type, assets.local_path,
                   assets.sha256, assets.status
            FROM assets
            WHERE assets.status = 'resolved'
              AND assets.kind IN ('image', 'audio')
            ORDER BY assets.id ASC
            """,
        ).fetchall()
        ready = skipped = errors = 0
        attempted = no_text = engine_unavailable = 0
        for row in rows:
            try:
                asset_id = int(row["id"])
                derivative_kind = "ocr" if row["kind"] == "image" else "transcript"
                path = self._asset_path(row["local_path"])
                try:
                    content_hash = _path_hash(path)
                except OSError:
                    path = None
                    content_hash = "missing"
                if path is not None and row["sha256"] != content_hash:
                    connection.execute(
                        "UPDATE assets SET sha256 = ? WHERE id = ?",
                        (content_hash, asset_id),
                    )
                existing = connection.execute(
                    """
                    SELECT status, error_code
                    FROM media_derivatives
                    WHERE asset_id = ? AND kind = ? AND content_hash = ?
                    """,
                    (asset_id, derivative_kind, content_hash),
                ).fetchone()
                if existing is not None:
                    if existing["status"] == "ready":
                        skipped += 1
                        continue
                    if (
                        existing["status"] == "skipped"
                        and existing["error_code"] == "NO_TEXT"
                    ):
                        skipped += 1
                        continue
                if attempted >= limit:
                    break
                attempted += 1
                if path is None:
                    self._store(
                        connection,
                        asset_id,
                        derivative_kind,
                        "skipped",
                        "",
                        "",
                        content_hash,
                        "ASSET_MISSING",
                    )
                    skipped += 1
                    continue
                if not self._extractor_available(derivative_kind):
                    self._store(
                        connection,
                        asset_id,
                        derivative_kind,
                        "skipped",
                        "",
                        "",
                        content_hash,
                        "ENGINE_UNAVAILABLE",
                    )
                    skipped += 1
                    engine_unavailable += 1
                    continue
                extraction_path = path
                if (
                    derivative_kind == "transcript"
                    and row["mime_type"] == "audio/silk"
                ):
                    if self._cache_root is None:
                        self._store(
                            connection,
                            asset_id,
                            derivative_kind,
                            "skipped",
                            "",
                            "",
                            content_hash,
                            "PLAYBACK_CACHE_UNAVAILABLE",
                        )
                        skipped += 1
                        continue
                    wav_path, source_hash, playback_error = prepare_silk_wav(
                        path,
                        cache_root=self._cache_root,
                        decoder=self._silk_decoder,
                    )
                    record_playback_state(
                        connection,
                        asset_id,
                        status="ready" if wav_path is not None else "error",
                        source_hash=source_hash,
                        error_code=playback_error,
                    )
                    if wav_path is None:
                        self._store(
                            connection,
                            asset_id,
                            derivative_kind,
                            "error",
                            "",
                            "",
                            source_hash or content_hash,
                            playback_error or "VOICE_DECODE_FAILED",
                        )
                        errors += 1
                        continue
                    extraction_path = wav_path
                # 释放上一段（sha256 更新 / silk 播放状态）写锁后再跑 OCR/whisper，
                # 避免慢计算期间持有写连接、阻塞 gateway 的 BEGIN IMMEDIATE 抢锁。
                connection.commit()
                try:
                    extractor = (
                        self._image_text_extractor
                        if derivative_kind == "ocr"
                        else self._audio_transcriber
                    )
                    text = (extractor(extraction_path) or "").strip()
                    if not text:
                        self._store(
                            connection,
                            asset_id,
                            derivative_kind,
                            "skipped",
                            "",
                            "",
                            content_hash,
                            "NO_TEXT",
                        )
                        skipped += 1
                        no_text += 1
                        continue
                    engine = (
                        "custom"
                        if extractor
                        not in {_rapidocr_extractor, _whisper_cpp_transcriber}
                        else derivative_kind
                    )
                    self._store(
                        connection,
                        asset_id,
                        derivative_kind,
                        "ready",
                        text,
                        engine,
                        content_hash,
                        None,
                    )
                    self._update_part_text(connection, asset_id, text)
                    ready += 1
                except Exception:
                    self._store(
                        connection,
                        asset_id,
                        derivative_kind,
                        "error",
                        "",
                        "",
                        content_hash,
                        "EXTRACTOR_FAILED",
                    )
                    errors += 1
            finally:
                connection.commit()
        return MediaEnrichmentResult(
            ready=ready,
            skipped=skipped,
            errors=errors,
            attempted=attempted,
            no_text=no_text,
            engine_unavailable=engine_unavailable,
            has_more=_has_pending_media(connection),
        )

    def _extractor_available(self, derivative_kind: str) -> bool:
        return (
            self._image_extractor_available
            if derivative_kind == "ocr"
            else self._audio_transcriber_available
        )

    def _asset_path(self, local_path: object) -> Path | None:
        if not isinstance(local_path, str) or not local_path:
            return None
        candidate = (self._asset_root / local_path).resolve()
        try:
            candidate.relative_to(self._asset_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _store(
        self,
        connection: sqlite3.Connection,
        asset_id: int,
        kind: str,
        status: str,
        text: str,
        engine: str,
        content_hash: str,
        error_code: str | None,
    ) -> None:
        now = self._clock()
        connection.execute(
            """
            INSERT INTO media_derivatives(
                asset_id, kind, status, text_content, engine,
                content_hash, error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, kind, content_hash) DO UPDATE SET
                status = excluded.status,
                text_content = excluded.text_content,
                engine = excluded.engine,
                error_code = excluded.error_code,
                updated_at = excluded.updated_at
            """,
            (asset_id, kind, status, text, engine, content_hash, error_code, now, now),
        )

    @staticmethod
    def _update_part_text(connection: sqlite3.Connection, asset_id: int, text: str) -> None:
        connection.execute(
            """
            UPDATE message_parts
            SET text_content = ?, status = 'resolved'
            WHERE id = (SELECT part_id FROM assets WHERE id = ?)
            """,
            (text, asset_id),
        )



_VLM_URL = "http://127.0.0.1:9123/v1/chat/completions"
_VLM_MODEL = "muse-glimmer-30b"
_VLM_TIMEOUT = 90


def _vlm_image_extractor(path: Path) -> str | None:
    """Extract text + describe image content via Muse Glimmer 30B (VLM, llama-swap 9123).

    Unlike RapidOCR (which only does OCR), a VLM understands the image:
    it describes the scene, objects, layout, and extracts all visible text
    in one call.  This is the primary image extractor; RapidOCR is the
    CPU-only fallback when LM Studio is unavailable.
    """
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    try:
        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return None
    payload = {
        "model": _VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请详细描述这张图片的内容，并完整提取图片中所有可见的文字信息。"
                            "如果是聊天截图，请逐行提取对话内容，保留发送者和时间。"
                            "如果是文档/报表截图，请按结构提取文字和数据。"
                            "输出格式：[图片描述] ... [提取文字] ..."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        _VLM_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_VLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
        text_content = result["choices"][0]["message"]["content"].strip()
        return text_content or None
    except Exception:
        return None


def _vlm_available() -> bool:
    """Check if LM Studio has the VLM model loaded."""
    try:
        with urllib.request.urlopen(f"{_VLM_URL.rsplit('/', 2)[0]}/models", timeout=5) as resp:
            models = json.loads(resp.read())
        return any(
            m.get("id", "").casefold() == _VLM_MODEL.casefold()
            for m in models.get("data", [])
        )
    except Exception:
        return False


_OCR_INSTANCE: object | None = None
_OCR_LOCK = threading.Lock()


def _rapidocr_extractor(path: Path) -> str | None:
    global _OCR_INSTANCE
    try:
        from rapidocr import RapidOCR
    except ImportError:
        return None
    # Reuse one RapidOCR instance: constructing it reloads the ONNX models
    # (det/cls/rec) on every image, which is slow and causes memory churn
    # that can crash a long media job.  Create it lazily once, then reuse.
    if _OCR_INSTANCE is None:
        with _OCR_LOCK:
            if _OCR_INSTANCE is None:
                _OCR_INSTANCE = RapidOCR()
    result = _OCR_INSTANCE(str(path))
    values = getattr(result, "txts", None)
    if values is None and isinstance(result, tuple) and len(result) > 1:
        values = result[1]
    if not values:
        return None
    return "\n".join(str(value).strip() for value in values if str(value).strip())


def _rapidocr_available() -> bool:
    try:
        from rapidocr import RapidOCR  # noqa: F401
    except ImportError:
        return False
    return True


def _whisper_cpp_transcriber(path: Path) -> str | None:
    executable = os.environ.get("CHATBOX_WHISPER_CPP") or os.environ.get(
        "GCHAT_WHISPER_CPP"
    )
    model = os.environ.get("CHATBOX_WHISPER_MODEL") or os.environ.get(
        "GCHAT_WHISPER_MODEL"
    )
    if not executable or not model:
        return None
    result = subprocess.run(
        [executable, "-m", model, "-f", str(path), "-nt", "-np"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        return None
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(("[", "whisper_"))
    ]
    return "\n".join(lines) or None


def _whisper_cpp_available() -> bool:
    executable = os.environ.get("CHATBOX_WHISPER_CPP") or os.environ.get(
        "GCHAT_WHISPER_CPP"
    )
    model = os.environ.get("CHATBOX_WHISPER_MODEL") or os.environ.get(
        "GCHAT_WHISPER_MODEL"
    )
    if not executable or not model:
        return False
    resolved_executable = shutil.which(executable) or executable
    return Path(resolved_executable).is_file() and Path(model).is_file()


def _has_pending_media(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM assets
        WHERE status = 'resolved'
          AND kind IN ('image', 'audio')
          AND NOT EXISTS (
                SELECT 1
                FROM media_derivatives
                WHERE media_derivatives.asset_id = assets.id
                  AND media_derivatives.kind = CASE
                        WHEN assets.kind = 'image' THEN 'ocr'
                        ELSE 'transcript'
                      END
                  AND media_derivatives.content_hash = COALESCE(assets.sha256, '')
                  AND (
                        media_derivatives.status = 'ready'
                        OR (
                            media_derivatives.status = 'skipped'
                            AND media_derivatives.error_code = 'NO_TEXT'
                        )
                      )
            )
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _path_hash(path: Path | None) -> str:
    if path is None:
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
