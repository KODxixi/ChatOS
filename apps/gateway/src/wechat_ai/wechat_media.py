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
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterator

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import pysilk


_MEDIA_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ACCOUNT_SUFFIX_PATTERN = re.compile(r"^(.+)_([0-9a-f]{4})$", re.IGNORECASE)
_KVCOMM_KEY_PATTERN = re.compile(
    r"^key_([0-9]{1,20})_.+\.statistic$",
    re.IGNORECASE,
)
_PACKED_INFO_LIMIT = 64 * 1024
_TENCENT_SILK_HEADER = b"\x02#!SILK_V3"
_V2_IMAGE_HEADER = bytes.fromhex("070856320807")
_IMAGE_MAGICS = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF",
    b"RIFF",
    b"wxgf",
)
_MAX_SILK_BYTES = 16 * 1024 * 1024
_MAX_PCM_BYTES = 64 * 1024 * 1024
_MAX_WAV_BYTES = 64 * 1024 * 1024
_DEFAULT_AUDIO_CACHE_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
_SILK_CACHE_LOCK = threading.Lock()
_BROWSER_IMAGE_MIMES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class _LimitedBytesIO(io.BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit

    def write(self, value: bytes | bytearray | memoryview) -> int:
        if self.tell() + len(value) > self._limit:
            raise ValueError("decoded SILK audio exceeds the size limit")
        return super().write(value)


def decode_silk_to_wav(
    payload: bytes,
    *,
    sample_rate: int = 24_000,
) -> bytes:
    if (
        not payload.startswith(_TENCENT_SILK_HEADER)
        or len(payload) > _MAX_SILK_BYTES
    ):
        raise ValueError("invalid Tencent SILK payload")
    if sample_rate not in {8_000, 12_000, 16_000, 24_000}:
        raise ValueError("unsupported SILK sample rate")

    pcm_output = _LimitedBytesIO(_MAX_PCM_BYTES)
    try:
        pysilk.decode(io.BytesIO(payload), pcm_output, sample_rate)
    except pysilk.SilkError as error:
        raise ValueError("invalid Tencent SILK payload") from error
    pcm = pcm_output.getvalue()
    if not pcm or len(pcm) % 2:
        raise ValueError("invalid decoded SILK audio")

    wav_output = io.BytesIO()
    with wave.open(wav_output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return wav_output.getvalue()


def prepare_silk_wav(
    source_path: Path,
    *,
    cache_root: Path,
    decoder: Callable[[bytes], bytes] = decode_silk_to_wav,
    cache_budget_bytes: int = _DEFAULT_AUDIO_CACHE_BUDGET_BYTES,
) -> tuple[Path | None, str | None, str | None]:
    if cache_budget_bytes < 1:
        raise ValueError("cache_budget_bytes must be positive")
    source_payload = _read_bounded_file(source_path, _MAX_SILK_BYTES)
    if source_payload is None:
        return None, None, "VOICE_DECODE_FAILED"
    source_hash = hashlib.sha256(source_payload).hexdigest()
    target_dir = _safe_audio_cache_dir(cache_root)
    if target_dir is None:
        return None, source_hash, "VOICE_DECODE_FAILED"
    target_path = target_dir / f"{source_hash}.wav"

    with _SILK_CACHE_LOCK:
        if _is_valid_cached_wav(target_path):
            return target_path, source_hash, None
        try:
            wav_payload = decoder(source_payload)
        except Exception:
            return None, source_hash, "VOICE_DECODE_FAILED"
        if not _is_valid_wav_payload(wav_payload):
            return None, source_hash, "VOICE_DECODE_FAILED"
        if (
            _audio_cache_size(target_dir, excluding=target_path)
            + len(wav_payload)
            > cache_budget_bytes
        ):
            return None, source_hash, "VOICE_CACHE_FULL"

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                dir=target_dir,
                prefix=f".{source_hash}.{os.getpid()}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(wav_payload)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target_path)
        except OSError:
            return None, source_hash, "VOICE_DECODE_FAILED"
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        return target_path, source_hash, None


def record_playback_state(
    connection: sqlite3.Connection,
    asset_id: int,
    *,
    status: str,
    source_hash: str | None,
    error_code: str | None,
) -> None:
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
        for entry in directory.iterdir():
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


def discover_v2_image_keys(
    media_root: str | Path,
) -> tuple[tuple[bytes, int], ...]:
    root = Path(media_root).resolve()
    if len(root.parents) < 2:
        return ()
    account_name = root.parents[1].name
    match = _ACCOUNT_SUFFIX_PATTERN.fullmatch(account_name)
    canonical_account = match.group(1) if match is not None else account_name
    if not canonical_account:
        return ()

    app_data_value = os.environ.get("APPDATA")
    if not app_data_value:
        return ()
    kvcomm = (
        Path(app_data_value)
        / "Tencent"
        / "xwechat"
        / "net"
        / "kvcomm"
    )
    if not kvcomm.is_dir():
        return ()

    keys: list[tuple[bytes, int]] = []
    seen: set[tuple[bytes, int]] = set()
    try:
        names = sorted(path.name for path in kvcomm.iterdir() if path.is_file())
    except OSError:
        return ()
    for name in names:
        key_match = _KVCOMM_KEY_PATTERN.fullmatch(name)
        if key_match is None:
            continue
        uin = key_match.group(1)
        aes_key = hashlib.md5(
            f"{uin}{canonical_account}".encode("utf-8")
        ).hexdigest()[:16].encode("ascii")
        candidate = (aes_key, int(uin) & 0xFF)
        if candidate not in seen:
            seen.add(candidate)
            keys.append(candidate)
    return tuple(keys)


def decode_v2_image(
    payload: bytes,
    *,
    aes_key: bytes,
    xor_key: int,
) -> bytes | None:
    if (
        len(payload) < 15
        or not payload.startswith(_V2_IMAGE_HEADER)
        or len(aes_key) != 16
        or not 0 <= xor_key <= 0xFF
    ):
        return None
    aes_size = int.from_bytes(payload[6:10], "little")
    xor_size = int.from_bytes(payload[10:14], "little")
    aligned_aes_size = (aes_size // 16 + 1) * 16
    body = payload[15:]
    raw_end = len(body) - xor_size
    if (
        aes_size < 1
        or aligned_aes_size > len(body)
        or raw_end < aligned_aes_size
    ):
        return None
    try:
        decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
        decrypted = decryptor.update(body[:aligned_aes_size]) + decryptor.finalize()
    except ValueError:
        return None
    padding = decrypted[-1]
    if (
        padding < 1
        or padding > 16
        or decrypted[-padding:] != bytes((padding,)) * padding
    ):
        return None

    aes_plaintext = decrypted[:aes_size]
    raw_middle = body[aligned_aes_size:raw_end]
    xor_tail = bytes(value ^ xor_key for value in body[raw_end:])
    decoded = aes_plaintext + raw_middle + xor_tail
    if not any(decoded.startswith(magic) for magic in _IMAGE_MAGICS):
        return None
    if decoded.startswith(b"RIFF") and decoded[8:12] != b"WEBP":
        return None
    return decoded


def extract_wxgf_content(
    payload: bytes,
) -> tuple[str, bytes] | None:
    if not payload.startswith(b"wxgf"):
        return None
    scan_end = min(len(payload), 4096)
    for offset in range(4, scan_end):
        if payload.startswith(b"\xff\xd8\xff", offset):
            return "image/jpeg", payload[offset:]
        if payload.startswith(b"\x89PNG\r\n\x1a\n", offset):
            return "image/png", payload[offset:]
    for marker in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        offset = payload.find(marker, 4)
        if offset >= 0:
            return "video/hevc", payload[offset:]
    return None


def extract_image_media_key(raw_value: object) -> str | None:
    if isinstance(raw_value, memoryview):
        packed_info = raw_value.tobytes()
    elif isinstance(raw_value, bytes):
        packed_info = raw_value
    else:
        return None
    if not packed_info or len(packed_info) > _PACKED_INFO_LIMIT:
        return None

    try:
        outer_fields = tuple(_protobuf_fields(packed_info))
    except ValueError:
        return None
    for field_number, wire_type, value in outer_fields:
        if field_number != 3 or wire_type != 2 or not isinstance(value, bytes):
            continue
        try:
            nested_fields = _protobuf_fields(value)
            for nested_number, nested_wire, nested_value in nested_fields:
                if (
                    nested_number != 4
                    or nested_wire != 2
                    or not isinstance(nested_value, bytes)
                ):
                    continue
                try:
                    media_key = nested_value.decode("ascii").casefold()
                except UnicodeDecodeError:
                    continue
                if _MEDIA_KEY_PATTERN.fullmatch(media_key) is not None:
                    return media_key
        except ValueError:
            continue
    return None


def extract_video_media_key(raw_value: object) -> str | None:
    """Extract the 32-hex media id from a WeChat 4.x video packed-info payload.

    Videos carry their media id as a 32-byte ASCII hex string inside the
    packed protobuf (a different field layout than images), so this walks
    every nested length-delimited field and returns the first exact match.
    """
    if isinstance(raw_value, memoryview):
        packed_info = raw_value.tobytes()
    elif isinstance(raw_value, bytes):
        packed_info = raw_value
    else:
        return None
    if not packed_info or len(packed_info) > _PACKED_INFO_LIMIT:
        return None

    seen: set[str] = set()

    def walk(payload: bytes) -> object:
        try:
            fields = tuple(_protobuf_fields(payload))
        except ValueError:
            return None
        for field_number, wire_type, value in fields:
            if wire_type != 2 or not isinstance(value, bytes):
                continue
            if len(value) == 32:
                try:
                    candidate = value.decode("ascii").casefold()
                except UnicodeDecodeError:
                    candidate = ""
                if (
                    candidate not in seen
                    and _MEDIA_KEY_PATTERN.fullmatch(candidate) is not None
                ):
                    seen.add(candidate)
                    return candidate
            nested = walk(value)
            if nested is not None:
                return nested
        return None

    return walk(packed_info)


def locate_video_by_context(
    media_root: str | Path,
    *,
    conversation_hash: str,
    sent_at: object,
    media_key: str,
) -> Path | None:
    """Locate a WeChat 4.x video file by the month-dir attach layout.

    Videos are stored under ``attach/<conversation-hash>/<YYYY-MM>/Video/``
    (or directly under the month dir) as ``<media-key>.mp4`` / ``.dat``.
    Returns the first candidate that exists inside ``media_root``.
    """
    normalized_hash = conversation_hash.casefold()
    normalized_key = media_key.casefold()
    if (
        _MEDIA_KEY_PATTERN.fullmatch(normalized_hash) is None
        or _MEDIA_KEY_PATTERN.fullmatch(normalized_key) is None
    ):
        return None
    try:
        timestamp = int(sent_at)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    try:
        month = datetime.fromtimestamp(timestamp).strftime("%Y-%m")
    except (OSError, OverflowError, ValueError):
        return None

    root = Path(media_root).resolve()
    for relative in (
        f"{normalized_hash}/{month}/Video/{normalized_key}.mp4",
        f"{normalized_hash}/{month}/Video/{normalized_key}.dat",
        f"{normalized_hash}/{month}/{normalized_key}.mp4",
        f"{normalized_hash}/{month}/{normalized_key}.dat",
    ):
        candidate = (root / relative).resolve()
        if _is_file_within(candidate, root):
            return candidate
    return None


def locate_image_dat(
    hardlink_database: str | Path,
    media_root: str | Path,
    *,
    media_key: str,
) -> Path | None:
    normalized_key = media_key.casefold()
    if _MEDIA_KEY_PATTERN.fullmatch(normalized_key) is None:
        return None

    database_path = Path(hardlink_database)
    root = Path(media_root).resolve()
    if not database_path.is_file() or not root.is_dir():
        return None
    connection = _connect_read_only(database_path)
    try:
        rows = connection.execute(
            """
            SELECT
                image_info.file_name,
                first_dir.username AS first_dir,
                second_dir.username AS second_dir
            FROM image_hardlink_info_v4 AS image_info
            JOIN dir2id AS first_dir ON first_dir.rowid = image_info.dir1
            JOIN dir2id AS second_dir ON second_dir.rowid = image_info.dir2
            WHERE lower(image_info.md5) = ?
               OR lower(image_info.file_name) IN (?, ?, ?)
            ORDER BY
                CASE lower(image_info.file_name)
                    WHEN ? THEN 0
                    WHEN ? THEN 1
                    ELSE 2
                END,
                image_info.file_size DESC
            """,
            (
                normalized_key,
                f"{normalized_key}.dat",
                f"{normalized_key}_h.dat",
                f"{normalized_key}_t.dat",
                f"{normalized_key}.dat",
                f"{normalized_key}_h.dat",
            ),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()

    for row in rows:
        file_name = _safe_path_segment(row["file_name"])
        first_dir = _safe_path_segment(row["first_dir"])
        second_dir = _safe_path_segment(row["second_dir"])
        if file_name is None or first_dir is None or second_dir is None:
            continue
        for candidate in (
            root / first_dir / second_dir / "Img" / file_name,
            root / first_dir / second_dir / file_name,
        ):
            resolved = candidate.resolve()
            if _is_file_within(resolved, root):
                return resolved
    return None


def locate_image_dat_by_context(
    media_root: str | Path,
    *,
    conversation_hash: str,
    sent_at: object,
    media_key: str,
) -> Path | None:
    normalized_hash = conversation_hash.casefold()
    normalized_key = media_key.casefold()
    if (
        _MEDIA_KEY_PATTERN.fullmatch(normalized_hash) is None
        or _MEDIA_KEY_PATTERN.fullmatch(normalized_key) is None
    ):
        return None
    try:
        timestamp = int(sent_at)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    try:
        month = datetime.fromtimestamp(timestamp).strftime("%Y-%m")
    except (OSError, OverflowError, ValueError):
        return None

    root = Path(media_root).resolve()
    image_dir = (root / normalized_hash / month / "Img").resolve()
    try:
        image_dir.relative_to(root)
    except ValueError:
        return None
    for suffix in ("_h.dat", "_t.dat", ".dat"):
        candidate = (image_dir / f"{normalized_key}{suffix}").resolve()
        if _is_file_within(candidate, root):
            return candidate
    return None


def load_voice_data(
    media_database: str | Path,
    *,
    server_id: object,
) -> bytes | None:
    try:
        normalized_server_id = int(server_id)
    except (TypeError, ValueError):
        return None
    database_path = Path(media_database)
    if not database_path.is_file():
        return None

    connection = _connect_read_only(database_path)
    try:
        row = connection.execute(
            """
            SELECT voice_data
            FROM VoiceInfo
            WHERE svr_id = ?
              AND length(voice_data) BETWEEN ? AND ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (
                normalized_server_id,
                len(_TENCENT_SILK_HEADER),
                _MAX_SILK_BYTES,
            ),
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    raw_value = row["voice_data"]
    voice_data = (
        raw_value.tobytes()
        if isinstance(raw_value, memoryview)
        else raw_value
        if isinstance(raw_value, bytes)
        else None
    )
    if (
        voice_data is None
        or len(voice_data) > _MAX_SILK_BYTES
        or not voice_data.startswith(_TENCENT_SILK_HEADER)
    ):
        return None
    return voice_data


def _protobuf_fields(
    payload: bytes,
) -> Iterator[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(payload):
        key, offset = _read_varint(payload, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number < 1:
            raise ValueError("invalid protobuf field")
        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
            yield field_number, wire_type, value
            continue
        if wire_type == 1:
            end = offset + 8
        elif wire_type == 2:
            length, offset = _read_varint(payload, offset)
            end = offset + length
        elif wire_type == 5:
            end = offset + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        if end > len(payload):
            raise ValueError("truncated protobuf field")
        value = payload[offset:end]
        offset = end
        yield field_number, wire_type, value


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(payload):
            raise ValueError("truncated protobuf varint")
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
    raise ValueError("protobuf varint is too long")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _safe_path_segment(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    segment = Path(value)
    if segment.name != value or value in {".", ".."}:
        return None
    return value


def _is_file_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate.is_file()


def add_media_readiness(
    connection: sqlite3.Connection,
    items: list[dict[str, Any]],
    *,
    asset_root: Path | None,
) -> None:
    assets = {
        int(asset["id"]): asset
        for item in items
        for asset in item.get("assets", [])
    }
    if not assets:
        return
    from .services.knowledge_projection_service import RETRIEVAL_INDEX_VERSION

    placeholders = ", ".join("?" for _asset_id in assets)
    rows = connection.execute(
        f"""
        SELECT
            assets.id,
            assets.part_id,
            assets.status,
            assets.kind,
            assets.mime_type,
            assets.local_path,
            assets.sha256,
            assets.metadata_json,
            media_derivatives.kind AS derivative_kind,
            media_derivatives.status AS derivative_status,
            media_derivatives.text_content AS derivative_text,
            media_derivatives.error_code AS derivative_error_code,
            retrieval_chunks.id AS retrieval_chunk_id
        FROM assets
        LEFT JOIN media_derivatives
          ON media_derivatives.id = (
                SELECT candidate.id
                FROM media_derivatives AS candidate
                WHERE candidate.asset_id = assets.id
                  AND candidate.content_hash = COALESCE(assets.sha256, '')
                ORDER BY candidate.updated_at DESC, candidate.id DESC
                LIMIT 1
          )
        LEFT JOIN retrieval_chunks
          ON retrieval_chunks.message_part_id = assets.part_id
         AND retrieval_chunks.index_version = ?
         AND retrieval_chunks.text_content = media_derivatives.text_content
        WHERE assets.id IN ({placeholders})
        """,
        (RETRIEVAL_INDEX_VERSION, *assets),
    ).fetchall()
    for row in rows:
        asset = assets[int(row["id"])]
        _add_asset_readiness(asset, row, asset_root=asset_root)


def _add_asset_readiness(
    asset: dict[str, Any],
    row: sqlite3.Row,
    *,
    asset_root: Path | None,
) -> None:
    safe_path = _managed_asset_path(asset_root, row["local_path"])
    has_resolved_source = row["status"] == "resolved" and safe_path is not None
    mime_type = str(row["mime_type"] or "").casefold()
    is_silk = row["kind"] == "audio" and mime_type == "audio/silk"
    processing_url = (
        f"/api/assets/{int(row['id'])}/playback"
        if has_resolved_source and is_silk
        else None
    )

    playback = _playback_metadata(row["metadata_json"])
    playback_matches = _playback_matches_source(playback, row["sha256"])
    playback_ready = playback_matches and playback.get("status") == "ready"
    playback_error = (
        str(playback.get("errorCode"))
        if playback_matches
        and playback.get("status") == "error"
        and isinstance(playback.get("errorCode"), str)
        else None
    )
    image_previewable = (
        has_resolved_source
        and row["kind"] == "image"
        and _is_browser_image_file(safe_path, mime_type)
    )
    source_previewable = bool(image_previewable or playback_ready)

    derivative = None
    derivative_error = None
    derivative_ready_and_indexed = False
    if row["derivative_kind"] is not None:
        derivative_status = str(row["derivative_status"])
        derivative_error = (
            str(row["derivative_error_code"])
            if derivative_status == "error"
            and row["derivative_error_code"] is not None
            else None
        )
        derivative_indexed = (
            derivative_status == "ready"
            and row["retrieval_chunk_id"] is not None
        )
        derivative_ready_and_indexed = derivative_indexed
        derivative = {
            "kind": str(row["derivative_kind"]),
            "status": derivative_status,
            "errorCode": (
                str(row["derivative_error_code"])
                if row["derivative_error_code"] is not None
                else None
            ),
            "indexed": derivative_indexed,
        }

    error_code = playback_error or derivative_error
    if error_code is not None:
        readiness_state = "error"
    elif derivative_ready_and_indexed:
        readiness_state = "processed"
    elif source_previewable:
        readiness_state = "previewable"
    elif has_resolved_source:
        readiness_state = "resolved"
    else:
        readiness_state = "discovered"

    asset.update(
        {
            "readinessState": readiness_state,
            "processingUrl": processing_url,
            "playbackUrl": processing_url if playback_ready else None,
            "errorCode": error_code,
            "derivative": derivative,
        }
    )


def _managed_asset_path(
    asset_root: Path | None,
    local_path: object,
) -> Path | None:
    if asset_root is None or not isinstance(local_path, str) or not local_path:
        return None
    root = asset_root.resolve()
    candidate = (root / local_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _playback_metadata(raw_value: object) -> dict[str, Any]:
    if not isinstance(raw_value, str):
        return {}
    try:
        metadata = json.loads(raw_value)
    except (TypeError, ValueError):
        return {}
    playback = metadata.get("_playback") if isinstance(metadata, dict) else None
    return playback if isinstance(playback, dict) else {}


def _playback_matches_source(
    playback: dict[str, Any],
    source_hash: object,
) -> bool:
    recorded_hash = playback.get("sourceHash")
    if recorded_hash is None:
        return playback.get("status") == "error"
    return (
        isinstance(recorded_hash, str)
        and isinstance(source_hash, str)
        and recorded_hash == source_hash
    )


def _is_browser_image_file(path: Path | None, mime_type: str) -> bool:
    if path is None or mime_type not in _BROWSER_IMAGE_MIMES:
        return False
    try:
        with path.open("rb") as source:
            header = source.read(12)
    except OSError:
        return False
    if mime_type == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
