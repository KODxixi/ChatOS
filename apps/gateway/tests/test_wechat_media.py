from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_bytes(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def _protobuf_int(field_number: int, value: int) -> bytes:
    return _varint(field_number << 3) + _varint(value)


def test_extracts_image_media_key_from_packed_info_image_md5() -> None:
    from wechat_ai.wechat_media import extract_image_media_key

    expected_key = "0123456789abcdef0123456789abcdef"
    decoy_key = b"fedcba9876543210fedcba9876543210"
    packed_info = (
        _protobuf_int(1, 106)
        + _protobuf_int(2, 14)
        + _protobuf_bytes(4, decoy_key)
        + _protobuf_bytes(
            3,
            _protobuf_int(1, 7)
            + _protobuf_bytes(4, expected_key.encode("ascii")),
        )
    )

    assert extract_image_media_key(packed_info) == expected_key


def test_locates_image_dat_through_hardlink_directory_ids(
    tmp_path: Path,
) -> None:
    from wechat_ai.wechat_media import locate_image_dat

    hardlink_database = tmp_path / "hardlink.db"
    media_root = tmp_path / "attach"
    expected_key = "0123456789abcdef0123456789abcdef"
    with sqlite3.connect(hardlink_database) as connection:
        connection.execute("CREATE TABLE dir2id (username TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE image_hardlink_info_v4 (
                md5_hash INTEGER,
                md5 TEXT,
                type INTEGER,
                file_name TEXT,
                file_size INTEGER,
                modify_time INTEGER,
                dir1 INTEGER,
                dir2 INTEGER,
                _rowid_ INTEGER PRIMARY KEY,
                extra_buffer BLOB
            )
            """
        )
        first_dir = connection.execute(
            "INSERT INTO dir2id(username) VALUES ('account-hash')"
        ).lastrowid
        second_dir = connection.execute(
            "INSERT INTO dir2id(username) VALUES ('2026-07')"
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO image_hardlink_info_v4(
                md5_hash, md5, type, file_name, file_size,
                modify_time, dir1, dir2, extra_buffer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    1,
                    expected_key,
                    2,
                    "stored-image.dat",
                    12,
                    100,
                    first_dir,
                    second_dir,
                    None,
                ),
                (
                    2,
                    "fedcba9876543210fedcba9876543210",
                    2,
                    "decoy.dat",
                    5,
                    200,
                    first_dir,
                    second_dir,
                    None,
                ),
            ),
        )
        connection.commit()

    expected_path = media_root / "account-hash" / "2026-07" / "stored-image.dat"
    expected_path.parent.mkdir(parents=True)
    expected_path.write_bytes(b"image-bytes")
    (expected_path.parent / "decoy.dat").write_bytes(b"decoy")

    assert locate_image_dat(
        hardlink_database,
        media_root,
        media_key=expected_key,
    ) == expected_path


def test_loads_voice_data_by_exact_server_id_not_conflicting_local_id(
    tmp_path: Path,
) -> None:
    from wechat_ai.wechat_media import load_voice_data

    media_database = tmp_path / "media_0.db"
    expected_voice = b"\x02#!SILK_V3" + b"\x00\x01synthetic-voice"
    with sqlite3.connect(media_database) as connection:
        connection.execute(
            """
            CREATE TABLE VoiceInfo (
                chat_name_id INTEGER,
                create_time INTEGER,
                local_id INTEGER,
                svr_id INTEGER,
                voice_data BLOB,
                data_index TEXT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO VoiceInfo(
                chat_name_id, create_time, local_id,
                svr_id, voice_data, data_index
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (1, 100, 9_002, 8_001, b"wrong-local-id-match", "0"),
                (1, 200, 41, 9_002, expected_voice, "0"),
            ),
        )
        connection.commit()

    result = load_voice_data(media_database, server_id=9_002)

    assert result == expected_voice
    assert result.startswith(b"\x02#!SILK_V3")


def test_rejects_voice_blob_larger_than_import_limit(tmp_path: Path) -> None:
    from wechat_ai.wechat_media import load_voice_data

    media_database = tmp_path / "media_0.db"
    oversized_voice = b"\x02#!SILK_V3" + b"\x00" * (16 * 1024 * 1024)
    with sqlite3.connect(media_database) as connection:
        connection.execute(
            "CREATE TABLE VoiceInfo (svr_id INTEGER, voice_data BLOB)"
        )
        connection.execute(
            "INSERT INTO VoiceInfo(svr_id, voice_data) VALUES (?, ?)",
            (9_003, oversized_voice),
        )
        connection.commit()

    assert load_voice_data(media_database, server_id=9_003) is None


def test_discovers_v2_image_key_from_local_kvcomm_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from wechat_ai.wechat_media import discover_v2_image_keys

    app_data = tmp_path / "appdata"
    kvcomm = app_data / "Tencent" / "xwechat" / "net" / "kvcomm"
    kvcomm.mkdir(parents=True)
    uin = "1234567890"
    (kvcomm / f"key_{uin}_synthetic.statistic").write_bytes(b"")
    media_root = (
        tmp_path
        / "xwechat_files"
        / "wxid_synthetic_abcd"
        / "msg"
        / "attach"
    )
    media_root.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(app_data))

    keys = discover_v2_image_keys(media_root)

    assert keys == (
        (
            hashlib.md5(
                f"{uin}wxid_synthetic".encode("utf-8")
            ).hexdigest()[:16].encode("ascii"),
            int(uin) & 0xFF,
        ),
    )


def test_decodes_wechat_v2_aes_raw_and_xor_sections() -> None:
    from wechat_ai.wechat_media import decode_v2_image

    ciphertext = bytes.fromhex(
        "d4e12999441d072c1c9ee54e5c19bddb"
        "c5f3adacf6c1f635b766158a6251cd7a"
    )
    payload = (
        bytes.fromhex("070856320807")
        + (20).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + b"\x01"
        + ciphertext
        + b"raw-middle"
        + bytes((0xFF ^ 0x5A, 0xD9 ^ 0x5A))
    )

    decoded = decode_v2_image(
        payload,
        aes_key=b"0123456789abcdef",
        xor_key=0x5A,
    )

    assert decoded == (
        bytes.fromhex(
            "ffd8ffe000104a46494600010203040506070809"
        )
        + b"raw-middle"
        + bytes.fromhex("ffd9")
    )
    assert (
        decode_v2_image(
            payload,
            aes_key=b"fedcba9876543210",
            xor_key=0x5A,
        )
        is None
    )


def test_resolves_cached_wechat_v2_image_without_network(tmp_path: Path) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.wechat_db import resolve_cached_image_assets

    encrypted = (
        bytes.fromhex("070856320807")
        + (20).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + b"\x01"
        + bytes.fromhex(
            "d4e12999441d072c1c9ee54e5c19bddb"
            "c5f3adacf6c1f635b766158a6251cd7a"
        )
        + b"raw-middle"
        + bytes((0xFF ^ 0x5A, 0xD9 ^ 0x5A))
    )
    expected = (
        bytes.fromhex("ffd8ffe000104a46494600010203040506070809")
        + b"raw-middle"
        + bytes.fromhex("ffd9")
    )
    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    encrypted_path = asset_root / hashlib.sha256(encrypted).hexdigest()
    encrypted_path.write_bytes(encrypted)

    connection = initialize_database(database_path)
    connection.execute(
        """
        INSERT INTO conversations(
            id, source_conversation_id, title, kind, message_count
        ) VALUES ('conversation-1', 'conversation-1', 'Media', 'direct', 1)
        """
    )
    message = connection.execute(
        """
        INSERT INTO messages(
            conversation_id, dedupe_key, sender_name, sent_at, type, content
        ) VALUES ('conversation-1', 'image-1', 'Alice', 1, 'image', '')
        """
    )
    part = connection.execute(
        """
        INSERT INTO message_parts(message_id, ordinal, kind, status)
        VALUES (?, 0, 'image', 'unresolved')
        """,
        (message.lastrowid,),
    )
    connection.execute(
        """
        INSERT INTO assets(
            part_id, ordinal, kind, status, file_name, mime_type,
            local_path, sha256
        ) VALUES (?, 0, 'image', 'unresolved', 'image.dat',
                  'application/x-wechat-image-v2', ?, ?)
        """,
        (part.lastrowid, encrypted_path.name, encrypted_path.name),
    )
    connection.commit()

    resolved, has_more = resolve_cached_image_assets(
        connection,
        asset_root=asset_root,
        image_keys=((b"0123456789abcdef", 0x5A),),
        limit=10,
    )
    row = connection.execute(
        """
        SELECT assets.status, assets.file_name, assets.mime_type,
               assets.local_path, message_parts.status AS part_status
        FROM assets
        JOIN message_parts ON message_parts.id = assets.part_id
        """
    ).fetchone()
    connection.close()

    assert resolved == 1
    assert has_more is False
    assert tuple(row)[:3] == ("resolved", "image.jpg", "image/jpeg")
    assert row["part_status"] == "resolved"
    assert (asset_root / row["local_path"]).read_bytes() == expected


def test_cached_image_resolution_prefers_local_high_quality_variant(
    tmp_path: Path,
) -> None:
    from wechat_ai.database import initialize_database
    from wechat_ai.wechat_db import resolve_cached_image_assets

    encrypted_jpeg = (
        bytes.fromhex("070856320807")
        + (20).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + b"\x01"
        + bytes.fromhex(
            "d4e12999441d072c1c9ee54e5c19bddb"
            "c5f3adacf6c1f635b766158a6251cd7a"
        )
        + b"raw-middle"
        + bytes((0xFF ^ 0x5A, 0xD9 ^ 0x5A))
    )
    invalid_base = bytes.fromhex("070856320807") + b"\x00" * 32
    conversation_hash = "a" * 32
    media_key = "b" * 32
    sent_at = 1_720_000_000_000
    month = datetime.fromtimestamp(sent_at // 1000).strftime("%Y-%m")
    media_root = tmp_path / "attach"
    image_dir = media_root / conversation_hash / month / "Img"
    image_dir.mkdir(parents=True)
    (image_dir / f"{media_key}.dat").write_bytes(invalid_base)
    (image_dir / f"{media_key}_h.dat").write_bytes(encrypted_jpeg)

    database_path = tmp_path / "library.sqlite"
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    cached = asset_root / hashlib.sha256(invalid_base).hexdigest()
    cached.write_bytes(invalid_base)
    connection = initialize_database(database_path)
    connection.execute(
        """
        INSERT INTO conversations(
            id, source_conversation_id, title, kind, message_count
        ) VALUES ('conversation-1', 'conversation-1', 'Media', 'direct', 2)
        """
    )
    blocker_message = connection.execute(
        """
        INSERT INTO messages(
            conversation_id, dedupe_key, sender_name, sent_at, type, content
        ) VALUES ('conversation-1', ?, 'Alice', ?, 'image', '')
        """,
        (f"shard:Msg_{'c' * 32}:server:0", sent_at),
    )
    blocker_part = connection.execute(
        """
        INSERT INTO message_parts(message_id, ordinal, kind, status)
        VALUES (?, 0, 'image', 'unresolved')
        """,
        (blocker_message.lastrowid,),
    )
    connection.execute(
        """
        INSERT INTO assets(
            part_id, ordinal, kind, status, file_name, mime_type,
            local_path, sha256, metadata_json
        ) VALUES (?, 0, 'image', 'unresolved', ?,
                  'application/x-wechat-image-v2', ?, ?,
                  '{"previewable":false}')
        """,
        (blocker_part.lastrowid, f"{'d' * 32}.dat", cached.name, cached.name),
    )
    message = connection.execute(
        """
        INSERT INTO messages(
            conversation_id, dedupe_key, sender_name, sent_at, type, content
        ) VALUES ('conversation-1', ?, 'Alice', ?, 'image', '')
        """,
        (f"shard:Msg_{conversation_hash}:server:1", sent_at),
    )
    part = connection.execute(
        """
        INSERT INTO message_parts(message_id, ordinal, kind, status)
        VALUES (?, 0, 'image', 'unresolved')
        """,
        (message.lastrowid,),
    )
    connection.execute(
        """
        INSERT INTO assets(
            part_id, ordinal, kind, status, file_name, mime_type,
            local_path, sha256
        ) VALUES (?, 0, 'image', 'unresolved', ?,
                  'application/x-wechat-image-v2', ?, ?)
        """,
        (part.lastrowid, f"{media_key}.dat", cached.name, cached.name),
    )
    connection.commit()

    resolved, has_more = resolve_cached_image_assets(
        connection,
        asset_root=asset_root,
        image_keys=((b"0123456789abcdef", 0x5A),),
        media_root=media_root,
        limit=1,
    )
    row = connection.execute(
        "SELECT status, mime_type, local_path FROM assets WHERE file_name <> ?",
        (f"{'d' * 32}.dat",),
    ).fetchone()
    connection.close()

    assert resolved == 1
    assert has_more is True
    assert tuple(row)[:2] == ("resolved", "image/jpeg")
    assert (asset_root / row["local_path"]).read_bytes().startswith(b"\xff\xd8\xff")


def test_extracts_embedded_image_or_hevc_from_wxgf() -> None:
    from wechat_ai.wechat_media import extract_wxgf_content

    jpeg = bytes.fromhex("ffd8ffe000104a464946") + b"synthetic"
    assert extract_wxgf_content(b"wxgf" + b"\x10" * 24 + jpeg) == (
        "image/jpeg",
        jpeg,
    )

    hevc = bytes.fromhex("0000000140010c01") + b"synthetic"
    assert extract_wxgf_content(b"wxgf" + b"\x20" * 24 + hevc) == (
        "video/hevc",
        hevc,
    )


def test_decodes_tencent_silk_into_mono_pcm_wav() -> None:
    import wave
    from io import BytesIO

    import pysilk

    from wechat_ai.wechat_media import decode_silk_to_wav

    pcm = b"\x00\x00" * 2_400
    silk = BytesIO()
    pysilk.encode(
        BytesIO(pcm),
        silk,
        24_000,
        24_000,
        tencent=True,
    )

    wav_payload = decode_silk_to_wav(silk.getvalue())

    with wave.open(BytesIO(wav_payload), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 2_400
        assert len(wav.readframes(wav.getnframes())) == len(pcm)


def test_extracts_video_media_key_from_nested_packed_info() -> None:
    from wechat_ai.wechat_media import extract_video_media_key

    expected_key = "5f038804d13d6812245f5e9aec52646c"
    # Mirrors the real WeChat 4.x video packed-info shape: a nested
    # length-delimited field carries the 32-hex media id.
    packed_info = (
        _protobuf_int(1, 530)
        + _protobuf_int(2, 1)
        + _protobuf_bytes(
            4,
            _protobuf_int(4, 800)
            + _protobuf_int(5, 1040)
            + _protobuf_bytes(2, expected_key.encode("ascii"))
            + _protobuf_int(9, 20),
        )
    )

    assert extract_video_media_key(packed_info) == expected_key


def test_extracts_video_media_key_rejects_non_hex_payloads() -> None:
    from wechat_ai.wechat_media import extract_video_media_key

    assert extract_video_media_key(_protobuf_bytes(2, b"not-a-video-key")) is None
    assert extract_video_media_key(b"\x08\x01\x10\x9e\t") is None
    assert extract_video_media_key(None) is None
    assert extract_video_media_key("not-bytes") is None


def test_locates_video_by_context_across_month_dir_layouts(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from wechat_ai.wechat_media import locate_video_by_context

    media_root = tmp_path / "attach"
    media_key = "5f038804d13d6812245f5e9aec52646c"
    conversation_hash = "94af6af9ea52b0635fbf89b730cb9ec3"
    sent_at = 1_781_000_000
    month = datetime.fromtimestamp(sent_at).strftime("%Y-%m")
    video_dir = (
        media_root / conversation_hash / month / "Video"
    )
    video_dir.mkdir(parents=True)
    (video_dir / f"{media_key}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    assert (
        locate_video_by_context(
            media_root,
            conversation_hash=conversation_hash,
            sent_at=sent_at,
            media_key=media_key,
        )
        == video_dir / f"{media_key}.mp4"
    )


def test_locate_video_by_context_probes_dat_fallback_and_rejects_traversal(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from wechat_ai.wechat_media import locate_video_by_context

    media_root = tmp_path / "attach"
    media_key = "5f038804d13d6812245f5e9aec52646c"
    conversation_hash = "94af6af9ea52b0635fbf89b730cb9ec3"
    sent_at = 1_781_000_000
    month = datetime.fromtimestamp(sent_at).strftime("%Y-%m")
    month_dir = media_root / conversation_hash / month
    month_dir.mkdir(parents=True)
    (month_dir / f"{media_key}.dat").write_bytes(b"\x00" * 64)

    assert (
        locate_video_by_context(
            media_root,
            conversation_hash=conversation_hash,
            sent_at=sent_at,
            media_key=media_key,
        )
        == month_dir / f"{media_key}.dat"
    )
    # traversal candidates must never escape the media root
    assert (
        locate_video_by_context(
            media_root,
            conversation_hash="..",
            sent_at=sent_at,
            media_key=media_key,
        )
        is None
    )
