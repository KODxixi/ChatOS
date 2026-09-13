from __future__ import annotations

import re


_INTERNAL_UNSUPPORTED_CONTENT = re.compile(
    r"^\[(?:不支持的消息类型:\s*-?\d+|文本内容不可读)\]$"
)
_FRIENDLY_UNSUPPORTED_CONTENT = "暂无法预览此消息"


def safe_message_display(
    message_type: object,
    content: object,
) -> tuple[str, str]:
    normalized_type = str(message_type or "")
    normalized_content = str(content or "")
    if (
        normalized_type.startswith("unsupported_")
        or normalized_type == "unsupported"
        or _INTERNAL_UNSUPPORTED_CONTENT.fullmatch(normalized_content) is not None
    ):
        return "unsupported", _FRIENDLY_UNSUPPORTED_CONTENT
    return normalized_type, normalized_content
