"""导入侧营销/广告群过滤：命中 noiseGroupKeywords 的群整个会话不入库。

与索引侧过滤（embedding_service._get_indexable_conversations）共用同一套
noiseGroupKeywords 规则；本测试锁定 wechat_db.import_sources 的行为。
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from fixtures.synthetic_sqlite import (
    _create_message_table,
    _message_table,
    create_synthetic_sources,
)
from wechat_ai import wechat_db
from wechat_ai.services import embedding_service as es

NOISE_KEYWORDS = ("秒杀", "福利")


def _library_db_with_noise_and_override(tmp_path: Path) -> Path:
    """建一个最小库：噪音群 + 人工显式开启的同名噪音群。"""
    db = tmp_path / "lib.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT, kind TEXT,
                                   message_count INTEGER, is_indexing_enabled INTEGER);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT, sender_id TEXT,
                              sender_name TEXT, sent_at INTEGER, type TEXT, content TEXT);
        CREATE TABLE conversation_overrides(conversation_id TEXT PRIMARY KEY,
                                           is_indexing_enabled INTEGER, domain TEXT, updated_at TEXT);
        """
    )
    # 普通噪音群（命中"交流群"）→ 应被索引过滤跳过
    conn.execute(
        "INSERT INTO conversations(id,title,kind,message_count,is_indexing_enabled) VALUES ('g1','业主交流群','group',50,1)"
    )
    # 人工显式开启的同名噪音群 → 必须保留（override=1 优先）
    conn.execute(
        "INSERT INTO conversations(id,title,kind,message_count,is_indexing_enabled) VALUES ('g2','业主交流群','group',50,1)"
    )
    conn.execute(
        "INSERT INTO conversation_overrides(conversation_id,is_indexing_enabled,domain,updated_at) VALUES ('g2',1,'project','2026-10-13')"
    )
    # 每个群 5 个不同发送者，避免触发成员数阈值
    for cid in ("g1", "g2"):
        for i in range(5):
            conn.execute(
                "INSERT INTO messages(id,conversation_id,sender_id,sender_name,sent_at,type,content) VALUES (?,?,?,?,?,'text',?)",
                (hash((cid, i)) % 10_000, cid, f"wx_{i}", f"用户{i}", 1_790_000_000_000 + i, "足够长的内容"),
            )
    conn.commit()
    conn.close()
    return db


def test_index_filter_explicit_override_keeps_noise_group(tmp_path: Path) -> None:
    """P4-3: conversation_overrides.is_indexing_enabled=1 的噪音群不被硬编码 skip。"""
    db = _library_db_with_noise_and_override(tmp_path)
    ids, reasons, skip = es._indexable_ids_and_reasons(sqlite3.connect(db))
    assert "g2" in ids          # 人工开启 → 保留
    assert "g1" not in ids      # 普通噪音群 → 跳过
    assert reasons["explicit_enabled"] == 1
    assert reasons["noise_keyword"] == 1


def _add_marketing_group(fixtures) -> str:
    """向合成源库追加一个营销群：标题「爆单福利秒杀群」。"""
    market_user = "market@chatroom"
    market_table = _message_table(market_user)

    with closing(sqlite3.connect(fixtures.session)) as conn:
        conn.execute(
            "INSERT INTO SessionTable (user_name, nick_name, last_timestamp) VALUES (?, ?, ?)",
            (market_user, "Marketing Session", 1700000005),
        )
        conn.commit()

    with closing(sqlite3.connect(fixtures.contact)) as conn:
        conn.execute(
            "INSERT INTO contact (user_name, nick_name, remark, local_type) VALUES (?, ?, ?, ?)",
            (market_user, "爆单福利秒杀群", "", 2),
        )
        conn.execute(
            "INSERT INTO chat_room (user_name, member_list) VALUES (?, ?)",
            (market_user, "alice,bob"),
        )
        conn.commit()

    with closing(sqlite3.connect(fixtures.message_shard)) as conn:
        conn.execute(
            "INSERT INTO Name2Id (id, user_name) VALUES (?, ?)",
            (4, market_user),
        )
        _create_message_table(conn, market_table)
        conn.execute(
            f'INSERT INTO "{market_table}" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                1,
                9004,
                1,
                100,
                2,
                1_700_000_004,
                0,
                0,
                "3 折秒杀，点击链接抢购",
                None,
                None,
            ),
        )
        conn.commit()
    return market_user


def test_marketing_group_is_excluded_from_import() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        fixtures = create_synthetic_sources(temp_path / "sources")
        market_user = _add_marketing_group(fixtures)
        app_database = temp_path / "library" / "wechat-ai.sqlite"

        result = wechat_db.import_sources(
            fixtures.root,
            app_database,
            group_filter_enabled=True,
            noise_group_keywords=NOISE_KEYWORDS,
        )

        assert result.inserted_messages > 0  # 正常群/单聊仍入库
        with closing(sqlite3.connect(app_database)) as conn:
            conversations = {
                row[0] for row in conn.execute("SELECT id FROM conversations")
            }
            messages_by_conv = dict(
                conn.execute(
                    "SELECT conversation_id, count(*) FROM messages GROUP BY conversation_id"
                )
            )
        assert market_user not in conversations
        assert market_user not in messages_by_conv
        # 对照组仍然存在
        assert fixtures.group_user_name in conversations
        assert fixtures.direct_user_name in conversations


def test_marketing_group_imported_when_filter_disabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        fixtures = create_synthetic_sources(temp_path / "sources")
        market_user = _add_marketing_group(fixtures)
        app_database = temp_path / "library" / "wechat-ai.sqlite"

        # 默认（过滤关闭）应保持原行为：营销群正常入库
        wechat_db.import_sources(fixtures.root, app_database)

        with closing(sqlite3.connect(app_database)) as conn:
            conversations = {
                row[0] for row in conn.execute("SELECT id FROM conversations")
            }
        assert market_user in conversations
