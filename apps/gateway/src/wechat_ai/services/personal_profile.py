"""Personalization profile for ChatOS todo extraction (owner: 史冠宇 / G爸).

Data-driven — built 2026-10-13 from the owner's real WeChat history.

Core idea: in GROUP chats a message is only a todo for the owner when it is
explicitly addressed to him (name / @ mention) or comes from a client or
leadership sender as a directive.  Direct (1:1) chats stay eligible unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "personalization.json"
_CONFIG_PATH = os.environ.get("CHATOS_PERSONALIZATION", str(_DEFAULT_PATH))

_AT_RE = re.compile(r"@([^\s@，。！、:：；]{1,20})")

_PROFILE: dict[str, Any] = {}
_LOADED = False
_MTIME: float = 0.0


def load_profile(force: bool = False) -> dict[str, Any]:
    """Load (and cache) the personalization profile. Never raises.

    The config is re-read automatically when the file's mtime changes, so
    editing personalization.json takes effect without restarting the gateway.
    """
    global _PROFILE, _LOADED, _MTIME
    if _LOADED and not force:
        try:
            mtime = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            return _PROFILE
        if mtime == _MTIME:
            return _PROFILE
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            _PROFILE = json.load(fh)
        logger.info(
            "personalization loaded: owner=%s assigners=%d",
            _PROFILE.get("owner", {}).get("name"),
            len(_PROFILE.get("relationship", {}).get("assigners", [])),
        )
    except Exception as exc:  # noqa: BLE001 - profile is optional
        logger.warning("personalization unavailable (%s); generic rules only", exc)
        _PROFILE = {}
    try:
        _MTIME = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        _MTIME = 0.0
    _LOADED = True
    return _PROFILE


def _tokens() -> tuple[list[str], list[str], list[str]]:
    p = load_profile()
    owner = p.get("owner", {}) or {}
    names = list(owner.get("addressed_as", []) or []) + list(owner.get("aliases_in_chat", []) or [])
    rel = p.get("relationship", {}) or {}
    high = list(rel.get("client", []) or []) + list(rel.get("leadership", []) or [])
    mid = list(rel.get("assigners", []) or [])
    return names, high, mid


def is_addressed_to_owner(text: str) -> bool:
    """True when the message names or @s the owner."""
    if not text:
        return False
    names, _, _ = _tokens()
    return any(tok and tok in text for tok in names)


def _tier_lists() -> dict[str, list[str]]:
    """Ordered tier -> people map (most senior first)."""
    p = load_profile()
    rel = p.get("relationship", {}) or {}
    return {
        "client": list(rel.get("client", []) or []),
        "leadership": list(rel.get("leadership", []) or []),
        "peer": list(rel.get("peer", []) or []),
        "colleague": list(rel.get("colleagues", []) or []),
        "assigner": list(rel.get("assigners", []) or []),
    }


_TIER_ORDER = ("client", "leadership", "peer", "colleague", "assigner")


def is_owner_message(sender_name: str) -> bool:
    """True when the message was sent BY the owner himself.

    His own outgoing messages are statements/replies, never todos assigned to
    him — they must not enter his board.
    """
    s = (sender_name or "").strip()
    if not s:
        return False
    p = load_profile()
    owner = p.get("owner", {}) or {}
    self_names = list(owner.get("aliases_in_chat", []) or []) + [owner.get("name") or ""]
    self_names += list(owner.get("addressed_as", []) or [])
    return any(n and (n == s or n in s) for n in self_names if n)


def sender_tier(sender_name: str) -> str:
    """One of client / leadership / peer / colleague / assigner / other."""
    s = (sender_name or "").strip()
    if not s:
        return "other"
    tiers = _tier_lists()
    for tier in _TIER_ORDER:
        if any(n and (n in s or s in n) for n in tiers.get(tier, [])):
            return tier
    return "other"


def is_directed_elsewhere(text: str) -> bool:
    """True when the message is clearly aimed at somebody other than the owner.

    Catches two strong signals:
      * an @mention of someone who is not the owner
      * a known colleague name appearing at the very start of the message
        (Chinese chat convention: "小任 发一下" addresses 小任)
    """
    if not text:
        return False
    p = load_profile()
    if not p:
        return False
    names, _, _ = _tokens()
    owner_names = [n for n in names if n]
    # @mention of a non-owner
    for m in _AT_RE.findall(text):
        if not any(o and (o in m) for o in owner_names):
            return True
    # leading colleague name
    rel = p.get("relationship", {}) or {}
    others = (
        list(rel.get("others", []) or [])
        + list(rel.get("assigners", []) or [])
        + list(rel.get("colleagues", []) or [])
        + list(rel.get("peer", []) or [])
    )
    head = text[:10]
    return any(o and o in head for o in others)


def gate_group_message(text: str, sender_name: str) -> bool:
    """Whether a GROUP message should be eligible as the owner's todo."""
    p = load_profile()
    if not p:
        return True
    rules = p.get("rules", {}) or {}
    if not rules.get("group_requires_addressing", True):
        return True
    if is_owner_message(sender_name):
        # His own message is never a todo for him.
        return False
    tier = sender_tier(sender_name)
    # 甲方 / 领导的消息永远算数（2026-10-13 裁决：领导很重要，不许豁免）。
    if tier in ("client", "leadership"):
        return True
    if rules.get("drop_directed_elsewhere", True) and is_directed_elsewhere(text):
        return False
    if is_addressed_to_owner(text):
        return True
    # 2026-10-13 裁决: nobody bypasses the addressing requirement in a group
    # any more.  A leader's team-wide design note ("柱头改一下") is not the
    # owner's todo; only a message that names him is.  The bypass can be
    # re-enabled through rules.leadership_must_be_addressed_in_group = false.
    if not rules.get("leadership_must_be_addressed_in_group", False):
        return tier in ("client", "leadership")
    return False


_DEFAULT_STALENESS = {"fresh_days": 3, "medium_days": 14, "low_days": 30}


def staleness_cap(sent_at_ms: int | None, now_ms: int | None = None) -> str | None:
    """Return the highest priority an item of this age may still hold.

    The legacy rule scored "今天/尽快/紧急" as high purely from the text, so a
    message from May saying "今天要改" stayed top-priority forever.  Priority
    now decays with age: only genuinely recent items can be high.

    Returns None when the item is fresh (no cap).
    """
    if not sent_at_ms:
        return None
    import time as _time
    now = now_ms if now_ms is not None else int(_time.time() * 1000)
    age_days = (now - int(sent_at_ms)) / 86400000.0
    s = (load_profile().get("rules", {}) or {}).get("staleness") or _DEFAULT_STALENESS
    if age_days <= float(s.get("fresh_days", 3)):
        return None
    if age_days <= float(s.get("medium_days", 14)):
        return "medium"
    return "low"


def priority_boost(text: str, sender_name: str, current: str, kind: str = "group",
                   sent_at_ms: int | None = None) -> str:
    """Promote priority by sender tier, and lift direct (1:1) chats.

    kind: 'direct' lifts to rules.direct_min_priority (owner asked for higher
    priority on private chats).
    """
    p = load_profile()
    if not p:
        return current
    rules = p.get("rules", {}) or {}
    order = {"low": 0, "medium": 1, "high": 2}
    best = current
    if kind == "direct":
        floor = rules.get("direct_min_priority")
        if floor and order.get(floor, 0) > order.get(best, 0):
            best = floor
    boost = rules.get("priority_boost", {}) or {}
    tier = sender_tier(sender_name)
    addressed = is_addressed_to_owner(text)
    target = None
    if tier in ("client", "leadership"):
        # 2026-10-13 裁决: 甲方 / 领导的消息一律为重点 (high)，
        # 无论是否点名、无论在群里还是在私聊。
        target = "high" if rules.get("client_leadership_always_high", True) else boost.get(tier)
    elif tier in ("peer", "colleague", "assigner") and addressed:
        target = boost.get(tier)
    if target and order.get(target, 1) > order.get(best, 0):
        best = target
    # Capping only applies to ordinary (non client/leadership) group chatter.
    if (kind == "group" and not addressed
            and tier not in ("client", "leadership")
            and order.get(best, 0) > order.get("medium", 1)):
        best = "medium"
    # Age decay — a "今天要办" from three months ago is not urgent today.
    cap = staleness_cap(sent_at_ms)
    if cap and order.get(best, 0) > order.get(cap, 0):
        best = cap
    return best


def rotate_expired(connection, now_ms: int | None = None) -> int:
    """Rotate todos older than rules.staleness.archive_days out of the board.

    The owner runs on a rolling 3-day cycle: a todo that has not been acted on
    inside the window leaves the active list (status -> 'completed', which is
    reversible) instead of piling up forever.  Returns the number rotated.
    """
    import time as _time
    p = load_profile()
    if not p:
        return 0
    s = (p.get("rules", {}) or {}).get("staleness") or {}
    days = float(s.get("archive_days", 0) or 0)
    if days <= 0:
        return 0
    now = now_ms if now_ms is not None else int(_time.time() * 1000)
    cutoff = now - int(days * 86400000)
    cur = connection.execute(
        "UPDATE tasks SET status='completed', updated_at=? "
        "WHERE status='todo' AND sent_at < ?",
        (str(now), cutoff),
    )
    if cur.rowcount:
        logger.info("rotation: %d stale todos archived (>%.0fd)", cur.rowcount, days)
    return int(cur.rowcount or 0)


def prompt_fragment() -> str:
    """Compact personalization block appended to the LLM filter prompt."""
    p = load_profile()
    if not p:
        return ""
    owner = p.get("owner", {}) or {}
    rel = p.get("relationship", {}) or {}
    names = "、".join((owner.get("addressed_as") or [])[:6])
    client = "、".join((rel.get("client") or [])[:4])
    lead = "、".join((rel.get("leadership") or [])[:6])
    assigners = "、".join((rel.get("assigners") or [])[:12])
    return "\n\n【主人翁画像】本库属于「" + str(owner.get("name")) + "」（微信名 " + ", ".join(owner.get("aliases_in_chat") or []) + "），群里常被称为：" + names + "。\n只保留**对他本人**的待办。群聊里如果一条消息是在对别人说话（例如叫别人发文件、评价别人的图），**不算他的待办**；只有点名他（史总/冠宇/@他）或甲方/领导（" + lead + "）直接交代的事项才算。\n甲方：" + client + "；领导：" + lead + "；常给他派活的同事：" + assigners + "。\n以下不算待办：群内技术讨论（模型/材质/光影/渲染参数）、表扬感谢寒暄、他自己汇报进度的话。"
