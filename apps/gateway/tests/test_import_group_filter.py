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

NOISE_KEYWORDS = ("秒杀", "福利")


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
