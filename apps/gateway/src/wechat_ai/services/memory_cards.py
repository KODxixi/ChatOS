r"""记忆卡片抽取管线（memory cards）。

从只读微信消息里抽「人/AI 都可读」的结构化记忆卡片，落盘到
`D:\ChatOS-Vault\knowledge\memory-cards\`（Markdown + index.json），
并在派生表 `memory_cards`（迁移 025）存在时同步 upsert，便于结构化查询。

设计要点：
- 只用本地 chat 模型（llama-swap 9123 · muse-glimmer-30b），零外部额度。
- LLM 调用通过 `complete` 回调注入：默认走 `services.llm_service.complete`，
  测试里传假回调即可，不发真实网络请求。
- 微信原始表只读；本服务只写自己的派生目录/派生表，不碰原始消息。
- 卡片去重键 = (类型, 主体归一化, 值归一化)；重复卡片合并来源、取最高置信度。

schema spec 见仓库 `docs/memory-cards-schema.md`。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# 卡片类型闭集（与既有 gchat_memories 类型对齐并扩展关系/项目/事件）。
CARD_TYPES = (
    "preference",    # 偏好/习惯/忌口/工具喜好
    "relationship",  # 人与人、人与项目的关系/角色
    "project",       # 项目本身的关键信息（名称、阶段、范围）
    "fact",          # 客观事实：数字、地址、时间、联系方式
    "decision",      # 已拍板的结论
    "constraint",    # 硬性约束/规范/红线
    "event",         # 将要发生的事、节点、待办
)

CARDS_DIRNAME = "memory-cards"
INDEX_NAME = "index.json"
_SCHEMA_VERSION = 1

# 噪音消息过滤：占位、系统、纯转发前缀等
_NOISE_RE = re.compile(
    r"^\[不支持的消息类型|^\[动画\]|^\[表情\]|^\[语音\]|^\[视频\]|^\[图片\]|"
    r"^\[链接\]|^\[文件\]|^\[聊天记录\]|^\[红包\]|^\[转账\]|^\[位置\]"
)
# 群消息里常见的 "wxid:\n正文" 前缀
_SENDER_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+:\w*\s*\n")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().lower()


@dataclass
class CardSource:
    message_id: int
    conversation_id: str
    sender: str
    sent_at: int
    conversation_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "conversation_title": self.conversation_title,
            "sender": self.sender,
            "sent_at": self.sent_at,
        }


@dataclass
class Card:
    type: str
    subject: str
    value: str
    confidence: float = 0.5
    sources: list[CardSource] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    status: str = "active"

    @property
    def dedupe_key(self) -> str:
        return f"{self.type}|{_norm(self.subject)}|{_norm(self.value)}"

    @property
    def id(self) -> str:
        return "mc-" + hashlib.sha1(self.dedupe_key.encode("utf-8")).hexdigest()[:12]

    def to_index_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "subject": self.subject,
            "value": self.value,
            "confidence": round(self.confidence, 2),
            "status": self.status,
            "tags": self.tags,
            "source_count": len(self.sources),
            "updated_at": self.updated_at,
        }

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_index_dict()
        d["created_at"] = self.created_at
        d["sources"] = [s.to_dict() for s in self.sources]
        return d


# ---------------------------------------------------------------------------
# 提示词与解析
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是一个微信会话记忆抽取器。给你一组编号后的聊天消息，从中抽取"
    "「值得长期记住、对人或 AI 都有用」的结构化记忆卡片。\n"
    "只抽有明确信息量的内容：偏好/习惯、人物或项目关系、项目关键信息、"
    "客观事实（数字/时间/地点）、已拍板的决策、硬性约束、即将到来的事件。\n"
    "不要抽取客套话、语气词、与记忆无关的闲聊。\n"
    '严格输出一个 JSON 数组，不要输出任何额外文字、不要 ``` 包裹。每个元素形如：\n'
    '{"type":"preference|relationship|project|fact|decision|constraint|event",'
    '"subject":"主体(人名/项目名/对象)",'
    '"value":"一句话陈述,具体、可独立读懂",'
    '"confidence":0.0到1.0的小数,'
    '"tags":["简短标签"],'
    '"evidence":[来源消息编号]}\n'
    "没有可抽取内容时输出 []。"
)


def _clean_content(raw: str) -> str:
    text = (raw or "").strip()
    text = _SENDER_PREFIX_RE.sub("", text)
    return text


def _usable(content: str) -> bool:
    if not content or len(content) < 6:
        return False
    if _NOISE_RE.search(content):
        return False
    return True


def build_extraction_prompt(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """把消息列表编号，组装成 (system, user) 提示词。"""
    lines: list[str] = []
    for i, m in enumerate(messages):
        sender = m.get("sender_name") or "未知"
        title = m.get("title") or m.get("conversation_id") or ""
        lines.append(f"[{i}] ({title} | {sender}) {m.get('content','')}")
    user = "以下是聊天消息，请抽取记忆卡片：\n" + "\n".join(lines)
    return _SYSTEM_PROMPT, user


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    """从模型输出里稳健地取出 JSON 数组。"""
    if not raw:
        return []
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return []
    blob = raw[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def parse_llm_cards(
    raw: str,
    messages: list[dict[str, Any]],
    *,
    now_ms: int | None = None,
) -> list[Card]:
    """把模型 JSON 输出解析成 Card 列表（附带真实来源消息）。"""
    now_ms = now_ms or int(time.time() * 1000)
    cards: list[Card] = []
    for row in _extract_json_array(raw):
        ctype = str(row.get("type") or "").strip()
        if ctype not in CARD_TYPES:
            continue
        subject = str(row.get("subject") or "").strip()
        value = str(row.get("value") or "").strip()
        if not subject or not value:
            continue
        try:
            confidence = float(row.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        tags = [str(t).strip() for t in (row.get("tags") or []) if str(t).strip()]

        sources: list[CardSource] = []
        evidence = row.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        for idx in evidence:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(messages):
                m = messages[i]
                sources.append(
                    CardSource(
                        message_id=int(m["id"]),
                        conversation_id=m.get("conversation_id", ""),
                        sender=m.get("sender_name", ""),
                        sent_at=int(m.get("sent_at") or 0),
                        conversation_title=m.get("title", ""),
                    )
                )
        cards.append(
            Card(
                type=ctype,
                subject=subject,
                value=value,
                confidence=confidence,
                sources=sources,
                tags=tags,
                created_at=now_ms,
                updated_at=now_ms,
            )
        )
    return cards


def dedupe_cards(cards: Iterable[Card]) -> list[Card]:
    """按 (类型,主体,值) 去重：合并来源、取最高置信度、合并 tags。"""
    merged: dict[str, Card] = {}
    for c in cards:
        key = c.dedupe_key
        if key not in merged:
            merged[key] = c
            continue
        exist = merged[key]
        existing_ids = {s.message_id for s in exist.sources}
        for s in c.sources:
            if s.message_id not in existing_ids:
                exist.sources.append(s)
        exist.confidence = max(exist.confidence, c.confidence)
        exist.updated_at = max(exist.updated_at, c.updated_at)
        for t in c.tags:
            if t not in exist.tags:
                exist.tags.append(t)
    return list(merged.values())


CompleteFn = Callable[..., str]


def extract_cards(
    messages: list[dict[str, Any]],
    *,
    complete: CompleteFn | None = None,
    now_ms: int | None = None,
) -> list[Card]:
    """对一批消息调用本地模型抽取卡片。LLM 不可用/解析失败时返回空列表。"""
    if complete is None:
        from . import llm_service

        # 与 _llm_filter_todos 等调用方一致：本地模型不可用时直接退化，
        # 不发起连接（也避免把 llm_service 的全局可用开关翻成 False）。
        if not llm_service.is_available(local_only=True):
            logger.warning("memory card extraction skipped: local LLM unavailable")
            return []
        complete = llm_service.complete
    from ..openai_compatible import OpenAICompatibleUnavailable

    usable = [m for m in messages if _usable(_clean_content(m.get("content", "")))]
    if not usable:
        return []
    system, user = build_extraction_prompt(usable)
    try:
        raw = complete(system, user, max_tokens=4096, temperature=0.1)
    except OpenAICompatibleUnavailable as exc:
        logger.warning("memory card extraction LLM unavailable: %s", exc)
        return []
    except Exception as exc:  # 网络/解析层异常不炸管线
        logger.warning("memory card extraction failed: %s", exc)
        return []
    cards = parse_llm_cards(raw, usable, now_ms=now_ms)
    return dedupe_cards(cards)


# ---------------------------------------------------------------------------
# 采样（只读）
# ---------------------------------------------------------------------------

def sample_messages(
    connection: sqlite3.Connection,
    *,
    direct_limit: int = 12,
    project_group_limit: int = 24,
    recent_days: int = 14,
) -> list[dict[str, Any]]:
    """只读采样：近期私聊 + 项目域群聊的有内容消息。

    只读连接调用；不写、不改原始表。
    """
    cutoff_ms = int(time.time() * 1000) - recent_days * 86_400_000
    out: list[dict[str, Any]] = []
    _SAMPLE_COLS = (
        "id", "conversation_id", "title", "kind", "sender_name", "sent_at", "content",
    )

    def _row(r) -> dict[str, Any]:
        # 兼容 sqlite3.Row 与默认 tuple 模式（row_factory 未设时 dict(r) 会崩）
        return {k: r[i] for i, k in enumerate(_SAMPLE_COLS)}

    direct = connection.execute(
        """
        SELECT m.id, m.conversation_id, c.title, c.kind, m.sender_name,
               m.sent_at, m.content
        FROM messages m JOIN conversations c ON c.id = m.conversation_id
        WHERE c.kind = 'direct'
          AND m.sent_at >= ?
          AND length(m.content) >= 6
        ORDER BY m.sent_at DESC
        LIMIT ?
        """,
        (cutoff_ms, direct_limit),
    ).fetchall()
    out.extend(_row(r) for r in direct)

    group = connection.execute(
        """
        SELECT m.id, m.conversation_id, c.title, c.kind, m.sender_name,
               m.sent_at, m.content
        FROM messages m JOIN conversations c ON c.id = m.conversation_id
        WHERE c.kind = 'group'
          AND (c.domain = 'project' OR c.title LIKE '%项目%')
          AND m.sent_at >= ?
          AND length(m.content) >= 6
        ORDER BY m.sent_at DESC
        LIMIT ?
        """,
        (cutoff_ms, project_group_limit),
    ).fetchall()
    out.extend(_row(r) for r in group)
    return out


# ---------------------------------------------------------------------------
# 落盘（Markdown + index.json）
# ---------------------------------------------------------------------------

def cards_root(vault_root: Path) -> Path:
    return Path(vault_root) / "knowledge" / CARDS_DIRNAME


def render_card_md(card: Card) -> str:
    type_cn = {
        "preference": "偏好", "relationship": "关系", "project": "项目",
        "fact": "事实", "decision": "决策", "constraint": "约束",
        "event": "事件",
    }.get(card.type, card.type)
    lines = [
        "---",
        f"id: {card.id}",
        f"type: {card.type}",
        f"type_cn: {type_cn}",
        f"subject: {card.subject}",
        f"confidence: {round(card.confidence, 2)}",
        f"status: {card.status}",
        f"tags: [{', '.join(card.tags)}]",
        f"updated_at: {card.updated_at}",
        "---",
        "",
        f"# [{type_cn}] {card.subject}",
        "",
        f"> {card.value}",
        "",
        "## 来源消息",
        "",
        "| 时间 | 会话 | 发送者 | 消息ID |",
        "|---|---|---|---|",
    ]
    for s in card.sources:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.sent_at / 1000)) if s.sent_at else "-"
        lines.append(f"| {when} | {s.conversation_title or s.conversation_id} | {s.sender} | {s.message_id} |")
    lines += ["", "---", "*由 ChatOS 记忆卡片管线自动抽取，原始消息只读*", ""]
    return "\n".join(lines)


def write_cards(
    cards: list[Card],
    vault_root: Path,
    *,
    now_ms: int | None = None,
) -> Path:
    """落盘：index.json + 每张卡片一个 .md。返回目录。"""
    now_ms = now_ms or int(time.time() * 1000)
    root = cards_root(vault_root)
    (root / "cards").mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict[str, Any]] = {}
    index_path = root / INDEX_NAME
    if index_path.exists():
        try:
            prev = json.loads(index_path.read_text(encoding="utf-8"))
            existing = {c["id"]: c for c in prev.get("cards", [])}
        except (json.JSONDecodeError, OSError):
            existing = {}

    by_id: dict[str, Card] = {}
    for c in cards:
        by_id[c.id] = c
        (root / "cards" / f"{c.id}.md").write_text(render_card_md(c), encoding="utf-8")

    # 合并旧卡片索引（新批次未覆盖的保留）
    index_cards: list[dict[str, Any]] = []
    for cid, old in existing.items():
        if cid not in by_id:
            index_cards.append(old)
    for c in by_id.values():
        index_cards.append(c.to_index_dict())
    index_cards.sort(key=lambda x: (x["type"], -x["updated_at"]))

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": now_ms,
        "count": len(index_cards),
        "cards": index_cards,
    }
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %d memory cards to %s", len(cards), root)
    return root


# ---------------------------------------------------------------------------
# 派生表 upsert（可选，表不存在则跳过）
# ---------------------------------------------------------------------------

def upsert_db(connection: sqlite3.Connection, cards: list[Card]) -> int:
    """若派生表 memory_cards 存在则 upsert；不存在返回 0。调用方负责事务/连接。"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_cards'"
    ).fetchone()
    if row is None:
        return 0
    now_ms = int(time.time() * 1000)
    n = 0
    for c in cards:
        connection.execute(
            """
            INSERT INTO memory_cards
              (id, card_type, subject, value, confidence, status,
               source_message_ids, conversation_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              confidence = excluded.confidence,
              source_message_ids = excluded.source_message_ids,
              updated_at = excluded.updated_at
            """,
            (
                c.id, c.type, c.subject, c.value, c.confidence, c.status,
                json.dumps([s.message_id for s in c.sources], ensure_ascii=False),
                c.sources[0].conversation_id if c.sources else "",
                c.created_at or now_ms, c.updated_at or now_ms,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# 端到端管线（采样 → 抽取 → 落盘 → 落表），带可见计数
# ---------------------------------------------------------------------------

def run_memory_cards_pass(
    connection: sqlite3.Connection,
    vault_root: Path,
    *,
    complete: CompleteFn | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """跑一遍记忆卡片全流程，并返回**可见计数**（不再静默 0 行）。

    链路：``sample_messages``（只读采样）→ ``extract_cards``（本地 LLM，
    不可用时返回空）→ ``write_cards``（Markdown + index.json，合并旧卡片）
    → ``upsert_db``（派生表 memory_cards）。

    返回::

        {"sampled": int, "extracted": int, "fileWritten": int,
         "dbUpserted": int, "llmDegraded": bool}

    - sampled>0 但 extracted==0 → llmDegraded=True（有素材却没抽到卡片，
      通常是 LLM 不可用或本次确实无记忆点），调用方据此标记降级。
    - 任何异常都被吞成 llmDegraded=True，绝不向上炸掉调用方管线。
    """
    now_ms = now_ms or int(time.time() * 1000)
    sampled = sample_messages(connection)
    cards: list[Card] = []
    try:
        cards = extract_cards(sampled, complete=complete, now_ms=now_ms)
    except Exception as exc:  # noqa: BLE001 - 抽取异常不炸管线
        logger.warning("memory_cards pass: extraction failed: %s", exc)
    file_written = 0
    if cards:
        write_cards(cards, vault_root, now_ms=now_ms)
        file_written = len(cards)
    db_upserted = upsert_db(connection, cards)
    llm_degraded = len(sampled) > 0 and len(cards) == 0
    report = {
        "sampled": len(sampled),
        "extracted": len(cards),
        "fileWritten": file_written,
        "dbUpserted": db_upserted,
        "llmDegraded": bool(llm_degraded),
    }
    logger.info("memory_cards pass: %s", report)
    return report
