"""记忆卡片管线单元测试：固定文本 fixture，不依赖真实消息与真实 LLM。"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wechat_ai.services import memory_cards as mc


def _msg(i: int, content: str, sender: str = "张三", title: str = "测试群") -> dict:
    return {
        "id": 1000 + i,
        "conversation_id": "conv-x",
        "title": title,
        "kind": "group",
        "sender_name": sender,
        "sent_at": 1_790_000_000_000 + i * 1000,
        "content": content,
    }


class TestParseLlmCards(unittest.TestCase):
    def test_parses_valid_json_array(self):
        messages = [
            _msg(0, "我习惯用鼠标 DPI 800"),
            _msg(1, "曹耀耀负责武汉项目造型"),
        ]
        raw = json.dumps([
            {"type": "preference", "subject": "Gary", "value": "习惯鼠标DPI 800",
             "confidence": 0.8, "tags": ["工具"], "evidence": [0]},
            {"type": "relationship", "subject": "曹耀耀", "value": "武汉项目造型负责人",
             "confidence": 0.9, "tags": ["项目"], "evidence": [1]},
        ], ensure_ascii=False)
        cards = mc.parse_llm_cards(raw, messages, now_ms=1)
        self.assertEqual(len(cards), 2)
        by_type = {c.type: c for c in cards}
        self.assertIn("preference", by_type)
        self.assertIn("relationship", by_type)
        # 来源消息真实挂上
        self.assertEqual(by_type["preference"].sources[0].message_id, 1000)
        self.assertEqual(by_type["relationship"].sources[0].message_id, 1001)

    def test_ignores_bad_type_and_empty_fields(self):
        messages = [_msg(0, "随便一句")]
        raw = json.dumps([
            {"type": "bogus", "subject": "x", "value": "y", "evidence": [0]},
            {"type": "fact", "subject": "", "value": "无主体", "evidence": [0]},
            {"type": "fact", "subject": "某地", "value": "", "evidence": [0]},
        ], ensure_ascii=False)
        self.assertEqual(mc.parse_llm_cards(raw, messages), [])

    def test_extracts_array_even_with_prose_around(self):
        messages = [_msg(0, "明天改拱券")]
        raw = '好的，结果如下：\n[{"type":"event","subject":"武汉项目","value":"明天改层叠拱券","confidence":0.7,"evidence":[0]}]\n以上。'
        cards = mc.parse_llm_cards(raw, messages)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].type, "event")

    def test_empty_and_garbage_returns_empty(self):
        self.assertEqual(mc.parse_llm_cards("", [_msg(0, "x")]), [])
        self.assertEqual(mc.parse_llm_cards("not json at all", [_msg(0, "x")]), [])


class TestDedupe(unittest.TestCase):
    def test_merges_same_key(self):
        a = mc.Card(type="fact", subject="杭州咏滨", value="A1栋成交价8万", confidence=0.6,
                    sources=[mc.CardSource(1, "c", "s", 1)])
        b = mc.Card(type="fact", subject="杭州咏滨", value="a1栋 成交价 8万", confidence=0.9,
                    sources=[mc.CardSource(2, "c", "s", 2)])
        out = mc.dedupe_cards([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].confidence, 0.9)  # 取最高
        self.assertEqual(len(out[0].sources), 2)  # 来源合并
        self.assertEqual(a.id, out[0].id)  # 同键同 id

    def test_different_key_kept(self):
        a = mc.Card(type="fact", subject="X", value="v1", sources=[])
        b = mc.Card(type="fact", subject="Y", value="v1", sources=[])
        self.assertEqual(len(mc.dedupe_cards([a, b])), 2)


class TestExtractCardsOrchestration(unittest.TestCase):
    def test_uses_injected_complete_and_filters_unusable(self):
        captured = {}

        def fake_complete(system, user, **kw):
            captured["system"] = system
            captured["user"] = user
            captured["kw"] = kw
            return json.dumps([
                {"type": "decision", "subject": "武汉项目", "value": "柱子改扁带平段",
                 "confidence": 0.8, "evidence": [0]},
            ], ensure_ascii=False)

        messages = [
            _msg(0, "我感觉柱子要改扁，侧面有平段"),
            _msg(1, "[不支持的消息类型: 10000]"),  # 应被过滤
        ]
        cards = mc.extract_cards(messages, complete=fake_complete, now_ms=1)
        self.assertEqual(len(cards), 1)
        self.assertIn("柱子要改扁", captured["user"])
        self.assertNotIn("不支持的消息类型", captured["user"])  # 噪音被剔除
        self.assertGreaterEqual(captured["kw"]["max_tokens"], 4096)

    def test_llm_unavailable_returns_empty(self):
        from wechat_ai.openai_compatible import OpenAICompatibleUnavailable

        def boom(system, user, **kw):
            raise OpenAICompatibleUnavailable("down")

        cards = mc.extract_cards([_msg(0, "随便")], complete=boom, now_ms=1)
        self.assertEqual(cards, [])


class TestWriteCards(unittest.TestCase):
    def test_writes_index_and_markdown(self):
        cards = [
            mc.Card(type="project", subject="武汉HYP", value="柱廊造型优化阶段",
                    confidence=0.8, tags=["造型"], created_at=1, updated_at=1,
                    sources=[mc.CardSource(100, "conv-1", "杨杰峰", 1_791_646_000_000, "HYP-武汉项目造型讨论群")]),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = mc.write_cards(cards, Path(td), now_ms=1)
            index = json.loads((root / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["count"], 1)
            self.assertEqual(index["cards"][0]["type"], "project")
            md_path = root / "cards" / f"{cards[0].id}.md"
            self.assertTrue(md_path.exists())
            md = md_path.read_text(encoding="utf-8")
            self.assertIn("武汉HYP", md)
            self.assertIn("来源消息", md)
            self.assertIn("100", md)

    def test_second_run_keeps_old_uncovered(self):
        with tempfile.TemporaryDirectory() as td:
            c1 = mc.Card(type="fact", subject="A", value="v1", created_at=1, updated_at=1, sources=[])
            mc.write_cards([c1], Path(td), now_ms=1)
            c2 = mc.Card(type="fact", subject="B", value="v2", created_at=2, updated_at=2, sources=[])
            mc.write_cards([c2], Path(td), now_ms=2)
            index = json.loads((Path(td) / "knowledge" / "memory-cards" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["count"], 2)


class TestMigration025(unittest.TestCase):
    def test_migration_sql_applies_and_upsert_works(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        sql = (Path(__file__).resolve().parents[1] / "src" / "wechat_ai"
               / "migrations" / "025_memory_cards.sql").read_text(encoding="utf-8")
        conn.executescript(sql)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_cards)")}
        self.assertIn("card_type", cols)
        self.assertIn("source_message_ids", cols)
        # upsert
        c = mc.Card(type="fact", subject="A", value="v", confidence=0.7,
                    sources=[mc.CardSource(1, "c", "s", 1)], created_at=1, updated_at=1)
        n = mc.upsert_db(conn, [c])
        self.assertEqual(n, 1)
        n2 = mc.upsert_db(conn, [c])
        self.assertEqual(n2, 1)
        cnt = conn.execute("SELECT count(*) FROM memory_cards").fetchone()[0]
        self.assertEqual(cnt, 1)  # upsert 不重复

    def test_upsert_no_table_returns_zero(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(mc.upsert_db(conn, [mc.Card(type="fact", subject="A", value="v", sources=[])]), 0)


if __name__ == "__main__":
    unittest.main()
