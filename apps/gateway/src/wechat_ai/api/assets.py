from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import wave
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from ..services.conversation_service import ConnectionFactory
from ..services.knowledge_service import resolve_asset_path
from ..wechat_media import decode_silk_to_wav
from .conversations import api_error_response

_MIME_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+_-]*/[A-Za-z0-9][A-Za-z0-9.+_-]*$"
)
_MAX_SILK_BYTES = 16 * 1024 * 1024
_MAX_WAV_BYTES = 64 * 1024 * 1024
_DEFAULT_AUDIO_CACHE_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
_SILK_CACHE_LOCK = threading.Lock()


class _VoiceCacheFull(Exception):
    def __init__(self, source_hash: str) -> None:
        self.source_hash = source_hash


def create_assets_router(
    connection_factory: ConnectionFactory,
    *,
    asset_root: Path,
    cache_root: Path,
    silk_decoder: Callable[[bytes], bytes] = decode_silk_to_wav,
    cache_budget_bytes: int = _DEFAULT_AUDIO_CACHE_BUDGET_BYTES,
) -> APIRouter:
    if cache_budget_bytes < 1:
        raise ValueError("cache_budget_bytes must be positive")
    router = APIRouter()

    @router.get("/assets/{asset_id}", response_model=None)
    def asset_content(asset_id: int) -> FileResponse | JSONResponse:
        row = _load_asset(connection_factory, asset_id)
        if row is None:
            return _asset_not_found()
        if row["status"] != "resolved":
            return api_error_response(
                status_code=409,
                code="ASSET_UNRESOLVED",
                message="Asset content has not been resolved.",
            )
        local_path = (
            str(row["local_path"]) if row["local_path"] is not None else None
        )
        safe_path = resolve_asset_path(asset_root, local_path)
        if safe_path is None:
            return _asset_not_found()
        return FileResponse(
            safe_path,
            media_type=_safe_response_mime(row["mime_type"]),
            filename=(
                str(row["file_name"])
                if row["file_name"] is not None
                else safe_path.name
            ),
            content_disposition_type="inline",
        )

    @router.head(
        "/assets/{asset_id}/playback",
        response_model=None,
        include_in_schema=False,
    )
    @router.get("/assets/{asset_id}/playback", response_model=None)
    def asset_playback(asset_id: int) -> FileResponse | JSONResponse:
        row = _load_asset(connection_factory, asset_id)
        if row is None:
            return _asset_not_found()
        if row["status"] != "resolved":
            return api_error_response(
                status_code=409,
                code="ASSET_UNRESOLVED",
                message="Asset content has not been resolved.",
            )
        mime_type = _safe_response_mime(row["mime_type"])
        if row["kind"] != "audio" or mime_type != "audio/silk":
            return api_error_response(
                status_code=409,
                code="ASSET_NOT_PLAYABLE",
                message="Asset does not require local audio conversion.",
            )

        local_path = (
            str(row["local_path"]) if row["local_path"] is not None else None
        )
        safe_path = resolve_asset_path(asset_root, local_path)
        if safe_path is None:
            return _asset_not_found()
        try:
            cached_path, source_hash = _cached_voice_path(
                cache_root=cache_root,
                source_path=safe_path,
                decoder=silk_decoder,
                cache_budget_bytes=cache_budget_bytes,
            )
        except _VoiceCacheFull as error:
            _record_playback_state(
                connection_factory,
                asset_id,
                status="error",
                source_hash=error.source_hash,
                error_code="VOICE_CACHE_FULL",
            )
            return _voice_cache_full_error()
        if cached_path is None:
            _record_playback_state(
                connection_factory,
                asset_id,
                status="error",
                source_hash=source_hash,
                error_code="VOICE_DECODE_FAILED",
            )
            return _voice_decode_error()
        _record_playback_state(
            connection_factory,
            asset_id,
            status="ready",
            source_hash=source_hash,
            error_code=None,
        )
        return FileResponse(
            cached_path,
            media_type="audio/wav",
            filename=_playback_file_name(row["file_name"]),
            content_disposition_type="inline",
            headers={"X-Media-Readiness": "previewable"},
        )

    return router


def _load_asset(
    connection_factory: ConnectionFactory,
    asset_id: int,
) -> sqlite3.Row | None:
    with closing(connection_factory()) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                id,
                kind,
                status,
                file_name,
                mime_type,
                local_path
            FROM assets
            WHERE id = ?
            """,
            (asset_id,),
        ).fetchone()


def _cached_voice_path(
    *,
    cache_root: Path,
    source_path: Path,
    decoder: Callable[[bytes], bytes],
    cache_budget_bytes: int,
) -> tuple[Path | None, str | None]:
    source_payload = _read_bounded_file(source_path, _MAX_SILK_BYTES)
    if source_payload is None:
        return None, None
    digest = hashlib.sha256(source_payload).hexdigest()

    target_dir = _safe_audio_cache_dir(cache_root)
    if target_dir is None:
        return None, digest
    target_path = target_dir / f"{digest}.wav"
    with _SILK_CACHE_LOCK:
        if _is_valid_cached_wav(target_path):
            return target_path, digest
        try:
            wav_payload = decoder(source_payload)
        except Exception:
            return None, digest
        if not _is_valid_wav_payload(wav_payload):
            return None, digest
        if (
            _audio_cache_size(target_dir, excluding=target_path)
            + len(wav_payload)
            > cache_budget_bytes
        ):
            raise _VoiceCacheFull(digest)

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                dir=target_dir,
                prefix=f".{digest}.{os.getpid()}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(wav_payload)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target_path)
        except OSError:
            return None, digest
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        return target_path, digest


def _record_playback_state(
    connection_factory: ConnectionFactory,
    asset_id: int,
    *,
    status: str,
    source_hash: str | None,
    error_code: str | None,
) -> None:
    with closing(connection_factory()) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(str(row[0]))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["_playback"] = {
            "errorCode": error_code,
            "sourceHash": source_hash,
            "status": status,
        }
        connection.execute(
            """
            UPDATE assets
            SET metadata_json = ?,
                sha256 = COALESCE(?, sha256)
            WHERE id = ?
            """,
            (
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                source_hash,
                asset_id,
            ),
        )
        connection.commit()


def _read_bounded_file(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as source:
            file_size = os.fstat(source.fileno()).st_size
            if file_size < 1 or file_size > limit:
                return None
            payload = source.read(limit + 1)
    except OSError:
        return None
    if len(payload) < 1 or len(payload) > limit or len(payload) != file_size:
        return None
    return payload


def _safe_audio_cache_dir(cache_root: Path) -> Path | None:
    try:
        resolved_root = cache_root.resolve()
        expected = resolved_root / "audio"
        expected.mkdir(parents=True, exist_ok=True)
        resolved_audio = expected.resolve()
        resolved_audio.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if resolved_audio != expected:
        return None
    return resolved_audio


def _audio_cache_size(directory: Path, *, excluding: Path) -> int:
    total = 0
    try:
        entries = directory.iterdir()
        for entry in entries:
            if entry == excluding or entry.suffix.casefold() != ".wav":
                continue
            resolved = entry.resolve()
            if resolved.parent != directory or not resolved.is_file():
                continue
            total += resolved.stat().st_size
    except OSError:
        return _DEFAULT_AUDIO_CACHE_BUDGET_BYTES
    return total


def _is_valid_cached_wav(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < 44 or size > _MAX_WAV_BYTES:
            return False
        if path.resolve().parent != path.parent.resolve():
            return False
        with wave.open(str(path), "rb") as cached:
            return _is_supported_wav(cached)
    except (OSError, EOFError, wave.Error):
        return False


def _is_valid_wav_payload(payload: object) -> bool:
    if not isinstance(payload, bytes) or not 44 <= len(payload) <= _MAX_WAV_BYTES:
        return False
    try:
        with wave.open(io.BytesIO(payload), "rb") as decoded:
            return _is_supported_wav(decoded)
    except (EOFError, wave.Error):
        return False


def _is_supported_wav(decoded: wave.Wave_read) -> bool:
    return (
        decoded.getnchannels() == 1
        and decoded.getsampwidth() == 2
        and decoded.getframerate() in {8_000, 12_000, 16_000, 24_000}
        and decoded.getnframes() > 0
    )


def _playback_file_name(value: object) -> str:
    if isinstance(value, str) and value.strip():
        stem = Path(value).name.rsplit(".", 1)[0].strip()
        if stem:
            return f"{stem}.wav"
    return "voice.wav"


def _asset_not_found() -> JSONResponse:
    return api_error_response(
        status_code=404,
        code="ASSET_NOT_FOUND",
        message="Asset content was not found.",
    )


def _voice_decode_error() -> JSONResponse:
    return api_error_response(
        status_code=422,
        code="VOICE_DECODE_FAILED",
        message="Voice content could not be prepared for playback.",
        retryable=True,
    )


def _voice_cache_full_error() -> JSONResponse:
    return api_error_response(
        status_code=507,
        code="VOICE_CACHE_FULL",
        message="Local voice playback cache has reached its size limit.",
        retryable=True,
    )


def _safe_response_mime(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _MIME_TYPE_PATTERN.fullmatch(value) is None:
        return None
    return value.casefold()
