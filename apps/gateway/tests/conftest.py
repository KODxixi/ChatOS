"""全局测试夹具。

**默认让本地模型「不可达」**（2026-10-15）。

背景：organize 相关测试会真的去打本机的 9123 端点 —— 模型答不答得上看**这台机器
当时的状态**（llama-swap 在不在跑、模型是冷是热、冷却有没有过期）。后果有两个：

1. **时红时绿**：实测约 1/3 概率失败，且失败的用例会在同文件内漂移 —— 不同用例对
   「模型有没有答」的敏感度不同。不确定的检查会训练人忽略红灯。
2. **慢约十倍**：四个文件 30–60 秒 vs 5.8 秒，差别全在「等真实推理」上。

打桩打在 `_try_call`（真正发 HTTP 的那一层），而不是伪造 `is_available`：这样走的是
**真实的失败路径**（含冷却与降级），被测代码看到的行为与「这台机器没有本地模型」
完全一致 —— 也正是 CI 上的真实条件。

需要真实模型语义的用例请在自己用例内覆盖 `_try_call` / `complete`
（见 tests/test_llm_service.py、tests/test_round2_regression.py）。
"""

import urllib.error

import pytest


@pytest.fixture(autouse=True)
def _local_llm_unreachable(monkeypatch):
    """让本地模型的 HTTP 调用必然失败，使测试不依赖本机是否跑着模型。"""
    from wechat_ai.services import llm_service

    def _unreachable(*args, **kwargs):
        raise urllib.error.URLError("conftest: local LLM unreachable in tests")

    monkeypatch.setattr(llm_service, "_try_call", _unreachable)
