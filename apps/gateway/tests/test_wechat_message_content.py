from __future__ import annotations

from wechat_ai import wechat_db


def test_valid_utf8_text_message_remains_searchable_text() -> None:
    assert wechat_db._normalize_message_content(
        1,
        "\u9879\u76ee\u8fdb\u5ea6",
    ) == ("text", "\u9879\u76ee\u8fdb\u5ea6")


def test_binary_payload_is_not_exposed_as_text() -> None:
    assert wechat_db._normalize_message_content(
        1,
        b"\xff\xfe\x00\x01binary",
    ) == ("unsupported_1", "[\u6587\u672c\u5185\u5bb9\u4e0d\u53ef\u8bfb]")


def test_composite_wechat_message_type_uses_low_32_bit_kind() -> None:
    assert wechat_db._normalize_message_content(
        (6 << 32) | 49,
        "<msg><appmsg /></msg>",
    ) == ("attachment", "[附件]")
    assert wechat_db._normalize_message_content(
        (9 << 32) | 3,
        "",
    ) == ("image", "[图片]")
