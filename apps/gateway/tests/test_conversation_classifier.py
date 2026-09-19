from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from wechat_ai.database import initialize_database
from wechat_ai.services.conversation_classifier import (
    classify_all_overrides,
    classify_conversation,
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("项目方案需要核对模型数据并准备交付", ("work", None, "auto")),
        ("客户项目方案模型数据交付；酒店、餐饮和物业另行对接", ("work", None, "auto")),
        ("爸爸今晚回家吃饭，妈妈和孩子都在等你", ("personal", None, "auto")),
        ("好的，收到", ("personal", None, "needs_review")),
    ],
)
def test_direct_relationship_is_inferred_from_content(content, expected):
    assert classify_conversation("普通联系人", "direct", sample_content=content) == expected


def test_override_classifier_samples_more_than_three_latest_messages(tmp_path):
    with closing(initialize_database(tmp_path / "library.sqlite")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "INSERT INTO conversations(id,source_conversation_id,title,kind,message_count) VALUES ('direct-1','direct-1','普通联系人','direct',4)"
        )
        messages = [
            ("work", 1000, "项目方案模型数据需要准备交付"),
            ("casual-1", 2000, "好的"),
            ("casual-2", 3000, "收到"),
            ("casual-3", 4000, "嗯嗯"),
        ]
        connection.executemany(
            "INSERT INTO messages(conversation_id,dedupe_key,sender_name,sent_at,type,content) VALUES ('direct-1',?,'Tester',?,'text',?)",
            messages,
        )
        connection.commit()

        classify_all_overrides(connection, only_needs_review=False)

        row = connection.execute(
            "SELECT domain, classification_status FROM conversation_overrides WHERE conversation_id='direct-1'"
        ).fetchone()
        assert tuple(row) == ("work", "auto")
