"""Unit tests for wechat_ai.services.message_cleaning (stdlib unittest)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wechat_ai.services import message_cleaning as mc


class TestEmoji(unittest.TestCase):
    def test_shortcode(self):
        out = mc.strip_emoji("好的[旺柴]")
        self.assertNotIn("[旺柴]", out)

    def test_unicode_emoji(self):
        out = mc.strip_emoji("hello 👋")
        self.assertNotIn("👋", out)


class TestRealPerson(unittest.TestCase):
    def test_wxid_filtered(self):
        self.assertTrue(mc.looks_like_wxid("wxid_abc123"))

    def test_url_filtered(self):
        self.assertTrue(mc.is_non_person("https://example.com"))

    def test_plain_name_not_wxid(self):
        self.assertFalse(mc.looks_like_wxid("张伟"))

    def test_newsapp_filtered(self):
        self.assertTrue(mc.is_non_person("newsapp"))


class TestFiller(unittest.TestCase):
    def test_filler(self):
        self.assertTrue(mc.is_filler("哦"))

    def test_not_filler(self):
        self.assertFalse(mc.is_filler("明天开会讨论方案"))


class TestMentions(unittest.TestCase):
    def test_clean_mentions_removes_at(self):
        out = mc.clean_mentions("@张三 明天开会")
        self.assertNotIn("@", out)

    def test_strip_mentions(self):
        out = mc.strip_mentions("@李四 你好")
        self.assertNotIn("@李四", out)


class TestTruncate(unittest.TestCase):
    def test_truncate_long(self):
        out = mc.truncate("a" * 100, 50)
        self.assertLessEqual(len(out), 51)  # includes ellipsis

    def test_truncate_short(self):
        self.assertEqual(mc.truncate("hi", 50), "hi")


class TestPersonMap(unittest.TestCase):
    def test_resolve_fallback(self):
        self.assertEqual(mc.resolve_person_name("wxid_x", "张伟", {}), "张伟")


if __name__ == "__main__":
    unittest.main()
