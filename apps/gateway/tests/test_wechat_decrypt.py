from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import struct
from pathlib import Path


AES_256_KEY = bytes.fromhex(
    "603deb1015ca71be2b73aef0857d7781"
    "1f352c073b6108d72d9810a30914dff4"
)
NIST_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NIST_PLAINTEXT = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef"
    "f69f2445df4f9b17ad2b417be66c3710"
)
NIST_CIPHERTEXT = bytes.fromhex(
    "f58c4c04d6e5f1ba779eabfb5f7bfbd6"
    "9cfc4e967edb808d679f777bc6702c7d"
    "39f23369a9d9bacfa530e26304231461"
    "b2eb05e2c39be9fcda6c19078c6a9d1b"
)
UPDATED_IV = bytes.fromhex("101112131415161718191a1b1c1d1e1f")
UPDATED_PLAINTEXT = bytes.fromhex(
    "a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"
    "b0b1b2b3b4b5b6b7b8b9babbbcbdbebf"
    "c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
)
UPDATED_CIPHERTEXT = bytes.fromhex(
    "681a1f91fdd02ad8a7c950e3251c1b4f"
    "40d5090f962d73321c482826d11084d11"
    "34dff0f813432cabf920f2c3c3c45ef"
)


def _encrypted_page(
    ciphertext: bytes,
    iv: bytes,
    *,
    first_page: bool,
) -> bytes:
    prefix = bytes.fromhex("00112233445566778899aabbccddeeff") if first_page else b""
    return prefix + ciphertext + iv + bytes(64)


def test_decrypts_sqlcipher_pages_and_applies_the_last_committed_wal_frame(
    tmp_path: Path,
) -> None:
    from wechat_ai.wechat_decrypt import decrypt_sqlcipher_database

    source = tmp_path / "message.db"
    wal = tmp_path / "message.db-wal"
    destination = tmp_path / "decrypted.db"
    source.write_bytes(
        _encrypted_page(NIST_CIPHERTEXT[:32], NIST_IV, first_page=True)
        + _encrypted_page(NIST_CIPHERTEXT[:48], NIST_IV, first_page=False)
    )

    wal_salt = bytes.fromhex("1122334455667788")
    wal_header = struct.pack(
        ">IIII",
        0x377F0682,
        3_007_000,
        128,
        1,
    ) + wal_salt + bytes(8)
    committed_frame = (
        struct.pack(">II", 2, 2)
        + wal_salt
        + bytes(8)
        + _encrypted_page(
            UPDATED_CIPHERTEXT,
            UPDATED_IV,
            first_page=False,
        )
    )
    wal.write_bytes(wal_header + committed_frame)

    result = decrypt_sqlcipher_database(
        source,
        destination,
        key=AES_256_KEY,
        wal_path=wal,
        page_size=128,
        reserve_size=80,
    )

    assert result.base_pages == 2
    assert result.applied_wal_frames == 1
    assert result.committed_database_pages == 2
    assert destination.read_bytes() == (
        b"SQLite format 3\x00"
        + NIST_PLAINTEXT[:32]
        + bytes(80)
        + UPDATED_PLAINTEXT
        + bytes(80)
    )


def test_refreshes_only_importable_wechat_databases_with_stable_names(
    tmp_path: Path,
) -> None:
    from wechat_ai.wechat_decrypt import refresh_decrypted_sources

    encrypted_root = tmp_path / "encrypted"
    output_root = tmp_path / "decrypted"
    key_file = tmp_path / "all_keys.json"
    supported = (
        r"session\session.db",
        r"contact\contact.db",
        r"message\message_0.db",
        r"message\biz_message_0.db",
        r"message\message_resource.db",
        r"message\media_0.db",
        r"hardlink\hardlink.db",
    )
    keys: dict[str, dict[str, str]] = {}
    for relative in (*supported, r"favorite\favorite.db"):
        source = encrypted_root.joinpath(*relative.split("\\"))
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(bytes(16) + b"encrypted")
        keys[relative] = {"enc_key": AES_256_KEY.hex(), "salt": "00" * 16}
    key_file.write_text(json.dumps(keys), encoding="utf-8")

    plaintext_template = tmp_path / "template.sqlite"
    with sqlite3.connect(plaintext_template) as connection:
        connection.execute("CREATE TABLE verified (id INTEGER PRIMARY KEY)")
        connection.commit()

    calls: list[tuple[Path, Path, bytes, Path | None]] = []

    def fake_decryptor(
        source: Path,
        destination: Path,
        *,
        key: bytes,
        wal_path: Path | None,
        **_options: object,
    ) -> object:
        calls.append((source, destination, key, wal_path))
        shutil.copyfile(plaintext_template, destination)
        return object()

    result = refresh_decrypted_sources(
        encrypted_root,
        key_file,
        output_root,
        decryptor=fake_decryptor,
    )

    expected_names = {
        f"{hashlib.md5(relative.encode('utf-8')).hexdigest()[:12]}.db"
        for relative in supported
    }
    assert result.refreshed_sources == len(supported)
    assert result.skipped_sources == 1
    assert {path.name for path in output_root.glob("*.db")} == expected_names
    assert {
        source.relative_to(encrypted_root).as_posix()
        for source, _destination, _key, _wal in calls
    } == {relative.replace("\\", "/") for relative in supported}
    assert all(key == AES_256_KEY for _source, _destination, key, _wal in calls)


def test_refreshes_from_credential_references_without_reading_plaintext_key_file(
    tmp_path: Path,
) -> None:
    from wechat_ai.wechat_decrypt import refresh_decrypted_sources_from_credentials

    encrypted_root = tmp_path / "encrypted"
    output_root = tmp_path / "decrypted"
    relative = "message/message_0.db"
    source = encrypted_root / "message" / "message_0.db"
    source.parent.mkdir(parents=True)
    salt = bytes.fromhex("11" * 16)
    source.write_bytes(salt + b"encrypted")
    plaintext_template = tmp_path / "template.sqlite"
    with sqlite3.connect(plaintext_template) as connection:
        connection.execute("CREATE TABLE verified (id INTEGER PRIMARY KEY)")

    class Credentials:
        def get(self, target: str) -> str | None:
            assert target == "Gchat/WeChat/message/message_0.db"
            return AES_256_KEY.hex()

    calls: list[bytes] = []

    def fake_decryptor(
        _source: Path,
        destination: Path,
        *,
        key: bytes,
        **_options: object,
    ) -> object:
        calls.append(key)
        shutil.copyfile(plaintext_template, destination)
        return object()

    result = refresh_decrypted_sources_from_credentials(
        encrypted_root,
        [
            {
                "databaseId": relative,
                "saltHash": hashlib.sha256(salt).hexdigest(),
                "credentialTarget": f"Gchat/WeChat/{relative}",
            }
        ],
        Credentials(),
        output_root,
        decryptor=fake_decryptor,
    )

    assert result.refreshed_sources == 1
    assert result.skipped_sources == 0
    assert calls == [AES_256_KEY]
    assert not (tmp_path / "all_keys.json").exists()
