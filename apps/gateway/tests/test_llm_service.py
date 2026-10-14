"""filter_todos 回归契约（P0-2，修复入库于提交 668789b）。

缺陷原貌：``llm_service.filter_todos`` 内的
``from ..rag_prompt import load_system_prompt`` 是一条死代码——被缩进到
``if not items:`` 块内、且位于 ``return`` 之后。但 Python 只要在函数体里看到
这个名字被 ``import`` 绑定，就把它判为**函数局部变量**；于是非空批次首次引用
``load_system_prompt`` 即抛 ``UnboundLocalError``（"cannot access local
variable ... where it is not associated with a value"），organize 整理环节每批
必失败（网关日志刷屏 33 次）。

修复：把该 import 提到函数体顶部（docstring 之后、空 items 短路之前）。
本测试锁定修复后的契约，防止同类回归悄悄复发：

* 空 items 直接短路返回 ``[]``，且绝不触发 LLM / prompt 加载；
* 非空 items **绝不抛 UnboundLocalError**——即使 LLM 不可达。这是真正的回归
  锁：旧代码在这条路径上、``complete()`` 被调用之前就会抛 UnboundLocalError；
* 合法 JSON 数组响应按 index / is_todo / cleaned 长度（10≤len≤60）过滤；
* 响应无 JSON 数组、或 JSON 解析失败时抛 ``OpenAICompatibleUnavailable``
  （降级、不写缓存）。

不触网、不碰数据库：``llm_service.complete`` 与 ``rag_prompt.load_system_prompt``
全部 monkeypatch。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from wechat_ai.openai_compatible import OpenAICompatibleUnavailable
from wechat_ai.services import llm_service


def _patch_prompt(monkeypatch, stub: str = "[system]") -> None:
    # filter_todos 在函数体内执行 `from ..rag_prompt import load_system_prompt`
    # （调用时才绑定），因此打补丁模块属性即可生效。
    import wechat_ai.rag_prompt as rag_prompt

    monkeypatch.setattr(rag_prompt, "load_system_prompt", lambda name: stub)


def test_empty_items_returns_empty_without_calling_llm(monkeypatch) -> None:
    import wechat_ai.rag_prompt as rag_prompt

    def _boom_complete(*args, **kwargs):
        raise AssertionError("complete() must not be called for empty items")

    def _boom_prompt(name):
        raise AssertionError("load_system_prompt() must not be called for empty items")

    monkeypatch.setattr(rag_prompt, "load_system_prompt", _boom_prompt)
    monkeypatch.setattr(llm_service, "complete", _boom_complete)

    assert llm_service.filter_todos([]) == []


def test_nonempty_items_llm_unavailable_raises_unavailable_not_unbound(monkeypatch) -> None:
    """P0-2 核心回归锁。

    旧代码（import 在 return 之后的死代码）在这条路径上、``complete()`` 尚未
    被调用时就抛 UnboundLocalError；修复后应穿透到 LLM 调用，并按契约抛
    OpenAICompatibleUnavailable。若本测试在旧代码上运行，pytest.raises 会因
    UnboundLocalError 不是 OpenAICompatibleUnavailable 的子类而失败——
    即它真能捕获该缺陷，而非空转。
    """
    _patch_prompt(monkeypatch)

    def _boom_complete(*args, **kwargs):
        raise OpenAICompatibleUnavailable("local LLM unreachable")

    monkeypatch.setattr(llm_service, "complete", _boom_complete)

    items = [{"index": 0, "text": "整理明天下午项目周会的会议纪要并发出"}]
    with pytest.raises(OpenAICompatibleUnavailable):
        llm_service.filter_todos(items)


def test_valid_json_array_filters_by_index_istodo_and_cleaned_length(monkeypatch) -> None:
    _patch_prompt(monkeypatch)

    items = [
        {"index": 0, "text": "整理明天下午项目周会的会议纪要并发出"},
        {"index": 1, "text": "短"},
        {"index": 2, "text": "帮我约周四上午十点牙科复查挂号"},
    ]

    def fake_complete(system, user, **kwargs):
        return (
            '[{"index":0,"is_todo":true,"cleaned":"整理明天下午项目周会纪要并发出"},'
            '{"index":1,"is_todo":true,"cleaned":"短"},'
            '{"index":2,"is_todo":false,"cleaned":"不要这条"}]'
        )

    monkeypatch.setattr(llm_service, "complete", fake_complete)

    out = llm_service.filter_todos(items)
    # index 0：is_todo 且 cleaned 15 字（10≤len≤60）→ 保留，文本替换为 cleaned；
    # index 1：cleaned 仅 1 字 → 按长度规则剔除；
    # index 2：is_todo=false → 剔除。
    assert out == [{"index": 0, "text": "整理明天下午项目周会纪要并发出"}]


def test_response_without_json_array_raises_unavailable(monkeypatch) -> None:
    """响应里完全没有 JSON 数组 → 按契约抛 OpenAICompatibleUnavailable（降级）。"""
    _patch_prompt(monkeypatch)
    monkeypatch.setattr(
        llm_service, "complete", lambda system, user, **kw: "抱歉，这个我判断不了。"
    )

    with pytest.raises(OpenAICompatibleUnavailable):
        llm_service.filter_todos([{"index": 0, "text": "整理明天下午项目周会的会议纪要"}])


def test_malformed_json_array_raises_unavailable(monkeypatch) -> None:
    """有 JSON 数组外壳但内容无法解析 → 同样抛 OpenAICompatibleUnavailable。"""
    _patch_prompt(monkeypatch)
    monkeypatch.setattr(
        llm_service, "complete", lambda system, user, **kw: "[{index: 0, not valid json"
    )

    with pytest.raises(OpenAICompatibleUnavailable):
        llm_service.filter_todos([{"index": 0, "text": "整理明天下午项目周会的会议纪要"}])


# ---------------------------------------------------------------------------
# 2026-10-14：一次失败不再永久关闭本地 LLM
#
# 缺陷原貌：``complete()`` 的异常分支执行 ``_use_local_llm = False`` —— 进程级、
# 永久生效，直到网关重启。配合 30s 的 ``LLM_TIMEOUT``（短于 30B 冷加载实测的
# **44s**），第一次冷启动超时就把整个进程的 LLM 能力锁死：organize 与
# semantic_graph 从此稳定 ``degraded``（error_code=LLM_UNAVAILABLE），
# 而日志还把它误报成"端点不可达"。
#
# 修复后契约：失败只进入**有界冷却**，``_use_local_llm`` 不再被改写，到点自动恢复。
# ---------------------------------------------------------------------------


def test_timeout_hint_blames_cold_load_not_unreachable_endpoint() -> None:
    """超时不能报成"端点不可达"——那是把人引向错误方向的诊断。"""
    hint = llm_service._failure_hint(TimeoutError("timed out"))
    assert "超时" in hint
    assert "不可达" not in hint


def test_unreachable_endpoint_still_hints_endpoint_down() -> None:
    hint = llm_service._failure_hint(ConnectionRefusedError("connection refused"))
    assert "不可达" in hint


def test_failure_cools_down_then_recovers_without_disabling_llm(monkeypatch) -> None:
    """核心回归锁：一次失败只进冷却，不永久关闭，且到点自动恢复。"""
    monkeypatch.setattr(llm_service, "_use_local_llm", True)
    monkeypatch.setattr(llm_service, "_llm_retry_at", 0.0)

    def _boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(llm_service, "_try_call", _boom)

    assert llm_service.is_available() is True

    with pytest.raises(OpenAICompatibleUnavailable):
        llm_service.complete("system", "user")

    # 进入冷却 —— 但总开关没被关掉（旧实现正是在这里把它置 False）
    assert llm_service.is_available() is False
    assert llm_service._use_local_llm is True

    # 冷却到点后自动恢复，不需要重启进程
    monkeypatch.setattr(llm_service, "_llm_retry_at", 0.0)
    assert llm_service.is_available() is True
