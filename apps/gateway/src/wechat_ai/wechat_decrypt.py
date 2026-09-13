from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sqlite3
import struct
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .credential_store import CredentialStore


SQLITE_HEADER = b"SQLite format 3\x00"
DEFAULT_PAGE_SIZE = 4096
DEFAULT_RESERVE_SIZE = 80
_WAL_HEADER_SIZE = 32
_WAL_FRAME_HEADER_SIZE = 24
_IMPORTABLE_DATABASE = re.compile(
    r"^(?:session/session\.db|contact/contact\.db|"
    r"message/(?:biz_)?message_[0-9]+\.db|"
    r"message/media_[0-9]+\.db|"
    r"message/message_resource\.db|"
    r"hardlink/hardlink\.db)$"
)


class WechatDecryptError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DecryptResult:
    base_pages: int
    applied_wal_frames: int
    committed_database_pages: int | None


@dataclass(frozen=True, slots=True)
class RefreshResult:
    refreshed_sources: int
    skipped_sources: int


DatabaseDecryptor = Callable[..., object]


def decrypt_sqlcipher_database(
    source: str | Path,
    destination: str | Path,
    *,
    key: bytes,
    wal_path: str | Path | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    reserve_size: int = DEFAULT_RESERVE_SIZE,
) -> DecryptResult:
    source_path = Path(source)
    destination_path = Path(destination)
    _validate_layout(key, page_size, reserve_size)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    base_pages = 0
    with _AESDecryptContext(key) as aes_ctx, source_path.open("rb") as encrypted, destination_path.open("wb") as output:
        while page := encrypted.read(page_size):
            if len(page) != page_size:
                raise WechatDecryptError("encrypted database contains a partial page")
            base_pages += 1
            output.write(
                _decrypt_page(
                    page,
                    page_number=base_pages,
                    key=key,
                    reserve_size=reserve_size,
                    aes_ctx=aes_ctx,
                )
            )
    if base_pages == 0:
        raise WechatDecryptError("encrypted database is empty")

    applied_frames = 0
    committed_pages: int | None = None
    resolved_wal = Path(wal_path) if wal_path is not None else None
    if resolved_wal is not None and resolved_wal.is_file():
        applied_frames, committed_pages = _apply_committed_wal(
            resolved_wal,
            destination_path,
            key=key,
            page_size=page_size,
            reserve_size=reserve_size,
        )

    return DecryptResult(
        base_pages=base_pages,
        applied_wal_frames=applied_frames,
        committed_database_pages=committed_pages,
    )


def refresh_decrypted_sources(
    encrypted_root: str | Path,
    key_file: str | Path,
    output_root: str | Path,
    *,
    decryptor: DatabaseDecryptor = decrypt_sqlcipher_database,
) -> RefreshResult:
    encrypted_dir = Path(encrypted_root).resolve()
    keys = _load_keys(Path(key_file))
    destination_dir = Path(output_root)
    destination_dir.mkdir(parents=True, exist_ok=True)
    refreshed = 0
    skipped = 0

    for raw_relative, key_entry in sorted(keys.items()):
        relative = _normalize_relative_path(raw_relative)
        if relative is None or _IMPORTABLE_DATABASE.fullmatch(relative) is None:
            skipped += 1
            continue
        source = encrypted_dir.joinpath(*relative.split("/")).resolve()
        try:
            source.relative_to(encrypted_dir)
        except ValueError:
            skipped += 1
            continue
        if not source.is_file():
            skipped += 1
            continue

        key = _raw_key(key_entry)
        _verify_salt(source, key_entry)
        stable_name = hashlib.md5(
            relative.replace("/", "\\").encode("utf-8")
        ).hexdigest()[:12] + ".db"
        destination = destination_dir / stable_name
        wal_path = Path(f"{source}-wal")
        wal = wal_path if wal_path.is_file() else None
        _refresh_one_source(
            source,
            destination,
            key=key,
            wal_path=wal,
            decryptor=decryptor,
        )
        refreshed += 1

    return RefreshResult(
        refreshed_sources=refreshed,
        skipped_sources=skipped,
    )


def refresh_decrypted_sources_from_credentials(
    encrypted_root: str | Path,
    references: Sequence[Mapping[str, object]],
    credential_store: CredentialStore,
    output_root: str | Path,
    *,
    decryptor: DatabaseDecryptor = decrypt_sqlcipher_database,
) -> RefreshResult:
    encrypted_dir = Path(encrypted_root).resolve()
    destination_dir = Path(output_root)
    destination_dir.mkdir(parents=True, exist_ok=True)
    refreshed = 0
    skipped = 0
    for reference in sorted(
        references,
        key=lambda item: str(item.get("databaseId", "")),
    ):
        raw_database_id = reference.get("databaseId")
        relative = (
            _normalize_relative_path(raw_database_id)
            if isinstance(raw_database_id, str)
            else None
        )
        if relative is None or _IMPORTABLE_DATABASE.fullmatch(relative) is None:
            skipped += 1
            continue
        source = encrypted_dir.joinpath(*relative.split("/")).resolve()
        try:
            source.relative_to(encrypted_dir)
        except ValueError:
            skipped += 1
            continue
        if not source.is_file():
            skipped += 1
            continue
        target = reference.get("credentialTarget")
        salt_hash = reference.get("saltHash")
        if not isinstance(target, str) or not target or not isinstance(salt_hash, str):
            raise WechatDecryptError("wechat credential reference is invalid")
        with source.open("rb") as stream:
            actual_salt = stream.read(16)
        if (
            len(actual_salt) != 16
            or not hashlib.sha256(actual_salt).hexdigest() == salt_hash.casefold()
        ):
            raise WechatDecryptError("wechat database key does not match source")
        secret = credential_store.get(target)
        key = _credential_key(secret)
        stable_name = hashlib.md5(
            relative.replace("/", "\\").encode("utf-8")
        ).hexdigest()[:12] + ".db"
        destination = destination_dir / stable_name
        wal_path = Path(f"{source}-wal")
        _refresh_one_source(
            source,
            destination,
            key=key,
            wal_path=wal_path if wal_path.is_file() else None,
            decryptor=decryptor,
        )
        refreshed += 1
    return RefreshResult(refreshed_sources=refreshed, skipped_sources=skipped)


def refresh_project_sources_from_credentials(
    project_root: str | Path,
    output_root: str | Path,
    *,
    connection_factory: Callable[[], sqlite3.Connection],
    credential_store: CredentialStore,
) -> RefreshResult | None:
    root = Path(project_root)
    config_path = root / "config.json"
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WechatDecryptError("wechat source config is invalid") from exc
    if not isinstance(config, dict) or not isinstance(config.get("db_dir"), str):
        raise WechatDecryptError("wechat source config does not define db_dir")
    with closing(connection_factory()) as connection:
        rows = connection.execute(
            """
            SELECT database_id, salt_hash, credential_target
            FROM wechat_source_key_refs
            ORDER BY database_id
            """
        ).fetchall()
    references = [
        {
            "databaseId": str(row[0]),
            "saltHash": str(row[1]),
            "credentialTarget": str(row[2]),
        }
        for row in rows
    ]
    if not references:
        return None
    return refresh_decrypted_sources_from_credentials(
        config["db_dir"],
        references,
        credential_store,
        output_root,
    )


def refresh_project_sources(
    project_root: str | Path,
    output_root: str | Path,
) -> RefreshResult | None:
    root = Path(project_root)
    config_path = root / "config.json"
    key_path = root / "all_keys.json"
    if not config_path.is_file() or not key_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WechatDecryptError("wechat source config is invalid") from exc
    if not isinstance(config, dict) or not isinstance(config.get("db_dir"), str):
        raise WechatDecryptError("wechat source config does not define db_dir")
    return refresh_decrypted_sources(
        config["db_dir"],
        key_path,
        output_root,
    )


def _refresh_one_source(
    source: Path,
    destination: Path,
    *,
    key: bytes,
    wal_path: Path | None,
    decryptor: DatabaseDecryptor,
) -> None:
    for _attempt in range(3):
        before = _source_state(source, wal_path)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            decryptor(
                source,
                temporary,
                key=key,
                wal_path=wal_path,
            )
            after = _source_state(source, wal_path)
            if before != after:
                continue
            _validate_sqlite(temporary)
            os.replace(temporary, destination)
            return
        finally:
            temporary.unlink(missing_ok=True)
    raise WechatDecryptError("wechat database changed during refresh")


def _source_state(
    source: Path,
    wal_path: Path | None,
) -> tuple[tuple[int, int], tuple[int, int] | None]:
    source_stat = source.stat()
    wal_state: tuple[int, int] | None = None
    if wal_path is not None and wal_path.is_file():
        wal_stat = wal_path.stat()
        wal_state = (wal_stat.st_size, wal_stat.st_mtime_ns)
    return (source_stat.st_size, source_stat.st_mtime_ns), wal_state


def _validate_sqlite(path: Path) -> None:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise WechatDecryptError("decrypted database validation failed") from exc
    finally:
        connection.close()
    if result is None or result[0] != "ok":
        raise WechatDecryptError("decrypted database validation failed")


def _load_keys(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WechatDecryptError("wechat key file is invalid") from exc
    if not isinstance(raw, dict):
        raise WechatDecryptError("wechat key file is invalid")
    return {
        str(relative): entry
        for relative, entry in raw.items()
        if isinstance(entry, dict)
    }


def _normalize_relative_path(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _raw_key(entry: dict[str, Any]) -> bytes:
    value = entry.get("enc_key")
    if not isinstance(value, str):
        raise WechatDecryptError("wechat key entry is invalid")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise WechatDecryptError("wechat key entry is invalid") from exc
    if len(key) != 32:
        raise WechatDecryptError("wechat key entry is invalid")
    return key


def _credential_key(value: str | None) -> bytes:
    if not isinstance(value, str):
        raise WechatDecryptError("wechat credential is unavailable")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise WechatDecryptError("wechat credential is invalid") from exc
    if len(key) != 32:
        raise WechatDecryptError("wechat credential is invalid")
    return key


def _verify_salt(source: Path, entry: dict[str, Any]) -> None:
    value = entry.get("salt")
    if not isinstance(value, str):
        return
    try:
        expected = bytes.fromhex(value)
    except ValueError as exc:
        raise WechatDecryptError("wechat salt entry is invalid") from exc
    with source.open("rb") as stream:
        actual = stream.read(16)
    if expected != actual:
        raise WechatDecryptError("wechat database key does not match source")


def _validate_layout(key: bytes, page_size: int, reserve_size: int) -> None:
    if len(key) != 32:
        raise WechatDecryptError("SQLCipher raw key must contain 32 bytes")
    if page_size <= reserve_size + 16 or page_size % 16:
        raise WechatDecryptError("SQLCipher page layout is invalid")
    if reserve_size < 16:
        raise WechatDecryptError("SQLCipher reserve must contain an IV")


def _decrypt_page(
    page: bytes,
    *,
    page_number: int,
    key: bytes,
    reserve_size: int,
    aes_ctx: "_AESDecryptContext | None" = None,
) -> bytes:
    if page_number < 1:
        raise WechatDecryptError("SQLCipher page number is invalid")
    first_page = page_number == 1
    start = 16 if first_page else 0
    end = len(page) - reserve_size
    ciphertext = page[start:end]
    if not ciphertext or len(ciphertext) % 16:
        raise WechatDecryptError("SQLCipher page payload is invalid")
    iv = page[end:end + 16]
    if aes_ctx is not None:
        plaintext = aes_ctx.decrypt(ciphertext, iv=iv)
    else:
        plaintext = _aes_cbc_decrypt(ciphertext, key=key, iv=iv)
    prefix = SQLITE_HEADER if first_page else b""
    return prefix + plaintext + bytes(reserve_size)


def _apply_committed_wal(
    wal_path: Path,
    destination: Path,
    *,
    key: bytes,
    page_size: int,
    reserve_size: int,
) -> tuple[int, int | None]:
    frame_count, committed_pages, wal_salt = _committed_wal_state(
        wal_path,
        page_size=page_size,
    )
    if frame_count == 0 or committed_pages is None:
        return 0, None

    frame_size = _WAL_FRAME_HEADER_SIZE + page_size
    with _AESDecryptContext(key) as aes_ctx, wal_path.open("rb") as wal, destination.open("r+b") as output:
        wal.seek(_WAL_HEADER_SIZE)
        for _index in range(frame_count):
            header = wal.read(_WAL_FRAME_HEADER_SIZE)
            page = wal.read(page_size)
            if len(header) != _WAL_FRAME_HEADER_SIZE or len(page) != page_size:
                raise WechatDecryptError("wechat WAL contains a partial frame")
            if header[8:16] != wal_salt:
                raise WechatDecryptError("wechat WAL changed during refresh")
            page_number = struct.unpack(">I", header[:4])[0]
            if page_number < 1:
                raise WechatDecryptError("wechat WAL page number is invalid")
            output.seek((page_number - 1) * page_size)
            output.write(
                _decrypt_page(
                    page,
                    page_number=page_number,
                    key=key,
                    reserve_size=reserve_size,
                    aes_ctx=aes_ctx,
                )
            )
        output.truncate(committed_pages * page_size)
    return frame_count, committed_pages


def _committed_wal_state(
    wal_path: Path,
    *,
    page_size: int,
) -> tuple[int, int | None, bytes]:
    frame_size = _WAL_FRAME_HEADER_SIZE + page_size
    with wal_path.open("rb") as wal:
        header = wal.read(_WAL_HEADER_SIZE)
        if len(header) < _WAL_HEADER_SIZE:
            return 0, None, b""
        magic, _version, wal_page_size = struct.unpack(">III", header[:12])
        if magic not in {0x377F0682, 0x377F0683}:
            raise WechatDecryptError("wechat WAL header is invalid")
        if wal_page_size == 1:
            wal_page_size = 65_536
        if wal_page_size != page_size:
            raise WechatDecryptError("wechat WAL page size does not match database")
        wal_salt = header[16:24]
        committed_frame_count = 0
        committed_pages: int | None = None
        frame_index = 0
        while True:
            frame_header = wal.read(_WAL_FRAME_HEADER_SIZE)
            if not frame_header:
                break
            if len(frame_header) != _WAL_FRAME_HEADER_SIZE:
                break
            page = wal.read(page_size)
            if len(page) != page_size or frame_header[8:16] != wal_salt:
                break
            frame_index += 1
            database_pages = struct.unpack(">I", frame_header[4:8])[0]
            if database_pages:
                committed_frame_count = frame_index
                committed_pages = database_pages
        expected_minimum = _WAL_HEADER_SIZE + committed_frame_count * frame_size
        if wal_path.stat().st_size < expected_minimum:
            raise WechatDecryptError("wechat WAL is incomplete")
    return committed_frame_count, committed_pages, wal_salt


_bcrypt: Any = None
_unsigned_char_pointer = ctypes.POINTER(ctypes.c_ubyte)
_bcrypt_handle = ctypes.c_void_p
_bcrypt_ulong = ctypes.c_ulong


def _ensure_bcrypt() -> Any:
    global _bcrypt
    if _bcrypt is not None:
        return _bcrypt
    bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(_bcrypt_handle),
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        _bcrypt_ulong,
    ]
    bcrypt.BCryptOpenAlgorithmProvider.restype = ctypes.c_long
    bcrypt.BCryptSetProperty.argtypes = [
        _bcrypt_handle,
        ctypes.c_wchar_p,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        _bcrypt_ulong,
    ]
    bcrypt.BCryptSetProperty.restype = ctypes.c_long
    bcrypt.BCryptGetProperty.argtypes = [
        _bcrypt_handle,
        ctypes.c_wchar_p,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        ctypes.POINTER(_bcrypt_ulong),
        _bcrypt_ulong,
    ]
    bcrypt.BCryptGetProperty.restype = ctypes.c_long
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        _bcrypt_handle,
        ctypes.POINTER(_bcrypt_handle),
        _unsigned_char_pointer,
        _bcrypt_ulong,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        _bcrypt_ulong,
    ]
    bcrypt.BCryptGenerateSymmetricKey.restype = ctypes.c_long
    bcrypt.BCryptDecrypt.argtypes = [
        _bcrypt_handle,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        ctypes.c_void_p,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        _unsigned_char_pointer,
        _bcrypt_ulong,
        ctypes.POINTER(_bcrypt_ulong),
        _bcrypt_ulong,
    ]
    bcrypt.BCryptDecrypt.restype = ctypes.c_long
    bcrypt.BCryptDestroyKey.argtypes = [_bcrypt_handle]
    bcrypt.BCryptDestroyKey.restype = ctypes.c_long
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [_bcrypt_handle, _bcrypt_ulong]
    bcrypt.BCryptCloseAlgorithmProvider.restype = ctypes.c_long
    _bcrypt = bcrypt
    return bcrypt


class _AESDecryptContext:
    """Reusable AES-CBC context that releases the GIL during BCryptDecrypt.

    Creating the provider+key once instead of per page avoids tens of
    thousands of handle allocations. Releasing the GIL around BCryptDecrypt
    lets HTTP workers continue while the import job decrypts -- previously
    the GIL was held for the entire decrypt loop, causing apparent hangs.
    """

    def __init__(self, key: bytes) -> None:
        if os.name != "nt":
            raise WechatDecryptError("live WeChat decryption is supported on Windows")
        bcrypt = _ensure_bcrypt()
        self._bcrypt = bcrypt
        self._algorithm = _bcrypt_handle()
        self._key_handle = _bcrypt_handle()
        self._key_object: Any = None
        _check_status(
            bcrypt.BCryptOpenAlgorithmProvider(
                ctypes.byref(self._algorithm), "AES", None, 0
            ),
            "open AES provider",
        )
        try:
            chaining_mode = ctypes.create_unicode_buffer("ChainingModeCBC")
            _check_status(
                bcrypt.BCryptSetProperty(
                    self._algorithm,
                    "ChainingMode",
                    ctypes.cast(chaining_mode, _unsigned_char_pointer),
                    ctypes.sizeof(chaining_mode),
                    0,
                ),
                "set AES-CBC mode",
            )
            object_length = _bcrypt_ulong()
            returned = _bcrypt_ulong()
            _check_status(
                bcrypt.BCryptGetProperty(
                    self._algorithm,
                    "ObjectLength",
                    ctypes.cast(ctypes.byref(object_length), _unsigned_char_pointer),
                    ctypes.sizeof(object_length),
                    ctypes.byref(returned),
                    0,
                ),
                "read AES key object length",
            )
            self._key_object = (ctypes.c_ubyte * object_length.value)()
            key_buffer = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
            _check_status(
                bcrypt.BCryptGenerateSymmetricKey(
                    self._algorithm,
                    ctypes.byref(self._key_handle),
                    self._key_object,
                    object_length.value,
                    key_buffer,
                    len(key),
                    0,
                ),
                "create AES key",
            )
        except BaseException:
            self.close()
            raise

    def decrypt(self, ciphertext: bytes, *, iv: bytes) -> bytes:
        if len(iv) != 16 or len(ciphertext) % 16:
            raise WechatDecryptError("AES-CBC input is invalid")
        input_buffer = (ctypes.c_ubyte * len(ciphertext)).from_buffer_copy(ciphertext)
        iv_buffer = (ctypes.c_ubyte * len(iv)).from_buffer_copy(iv)
        output_buffer = (ctypes.c_ubyte * len(ciphertext))()
        output_size = _bcrypt_ulong()
        # ctypes automatically releases the GIL during the native BCryptDecrypt
        # call; reusing the key handle keeps per-page Python overhead minimal so
        # HTTP workers are not starved while a large database is being decrypted.
        status = self._bcrypt.BCryptDecrypt(
            self._key_handle,
            input_buffer,
            len(ciphertext),
            None,
            iv_buffer,
            len(iv),
            output_buffer,
            len(output_buffer),
            ctypes.byref(output_size),
            0,
        )
        _check_status(status, "decrypt AES-CBC payload")
        return bytes(output_buffer[: output_size.value])

    def close(self) -> None:
        if self._key_handle.value:
            self._bcrypt.BCryptDestroyKey(self._key_handle)
            self._key_handle = _bcrypt_handle()
        if self._algorithm.value:
            self._bcrypt.BCryptCloseAlgorithmProvider(self._algorithm, 0)
            self._algorithm = _bcrypt_handle()

    def __enter__(self) -> "_AESDecryptContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _aes_cbc_decrypt(ciphertext: bytes, *, key: bytes, iv: bytes) -> bytes:
    """One-shot decrypt (kept for tests / single-page callers)."""
    with _AESDecryptContext(key) as ctx:
        return ctx.decrypt(ciphertext, iv=iv)


def _check_status(status: int, operation: str) -> None:
    if status != 0:
        raise WechatDecryptError(f"Windows CNG failed to {operation}")
