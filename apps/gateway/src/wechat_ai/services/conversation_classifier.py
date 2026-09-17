"""Automatic conversation classification for ChatOS taxonomy.

Classifies conversations into domains (project/work/personal/service/subscription)
based on title patterns, conversation kind, and message content heuristics.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

Domain = str  # "project" | "work" | "personal" | "service" | "subscription"
ProjectRole = str  # "core" | "collab" | "client" | "vendor" | None


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    evaluated: int
    classified: int
    needs_review: int
    by_domain: dict[str, int]


# --- Keyword patterns ---

_PROJECT_PATTERNS = [
    r"项目", r"设计", r"建筑", r"方案", r"施工", r"幕墙", r"立面",
    r"景观", r"室内", r"规划", r"地产", r"园区", r"竞赛", r"投标",
    r"施工图", r"概念", r"立面", r"泛光", r"绿建", r"前策",
    r"HYP", r"海棠湾", r"咏滨", r"圣湘", r"火炬", r"保利",
    r"融创", r"绿城", r"建发", r"华润", r"中海", r"金茂",
    r"甲第", r"观潮", r"传麒", r"春来", r"咏舟",
]

_WORK_PATTERNS = [
    r"公司", r"团队", r"部门", r"工作群", r"管理", r"沟通群",
    r"协作", r"会议", r"内部", r"研发", r"讨论组", r"研讨",
    r"霍普", r"GOA", r"gad", r"UA", r"水石", r"天华",
    r"HR", r"行政", r"财务", r"法务", r"运营",
]

_SERVICE_PATTERNS = [
    r"福利", r"优惠", r"宠粉", r"客服", r"立减", r"券",
    r"团购", r"会员", r"服务群", r"售后", r"维修", r"安装",
    r"定制", r"工厂", r"门窗", r"木作", r"瓷砖", r"卫浴",
    r"家电", r"手机", r"银行", r"信用卡", r"保险", r"证券",
    r"医美", r"健身", r"餐饮", r"酒店", r"机票", r"租车",
    r"快递", r"物业", r"装修", r"保洁", r"搬家",
]

_SUBSCRIPTION_PATTERNS = [
    r"公众号", r"新闻", r"资讯", r"订阅", r"日报", r"周报",
    r"推送", r"通知", r"公告",
]

_PERSONAL_PATTERNS = [
    r"家人", r"亲友", r"朋友", r"同学", r"校友", r"老乡",
    r"群", r"约球", r"健身", r"游戏", r"吐槽", r"毕业",
    r"婚礼", r"生日",
]

# Compile patterns
_PROJECT_RE = re.compile("|".join(_PROJECT_PATTERNS), re.IGNORECASE)
_WORK_RE = re.compile("|".join(_WORK_PATTERNS), re.IGNORECASE)
_SERVICE_RE = re.compile("|".join(_SERVICE_PATTERNS), re.IGNORECASE)
_SUBSCRIPTION_RE = re.compile("|".join(_SUBSCRIPTION_PATTERNS), re.IGNORECASE)
_PERSONAL_RE = re.compile("|".join(_PERSONAL_PATTERNS), re.IGNORECASE)


def classify_conversation(
    title: str,
    kind: str,
    *,
    sample_content: str | None = None,
) -> tuple[Domain, ProjectRole | None, str]:
    """Classify a single conversation.

    Returns (domain, project_role, classification_status).
    """
    text = f"{title} {sample_content or ''}"

    # System/official accounts → subscription
    if kind == "system":
        return "subscription", None, "auto"

    # Direct (1:1) chats are NEVER marked as "project" — project domain is
    # reserved for group chats / multi-conversation projects. Direct chats
    # are classified as personal / work / service / subscription only.
    if kind == "direct":
        if _SERVICE_RE.search(text):
            return "service", None, "auto"
        if _SUBSCRIPTION_RE.search(text):
            return "subscription", None, "auto"
        if _WORK_RE.search(text):
            return "work", None, "auto"
        return "personal", None, "auto"

    # Service patterns (strong signal, groups only)
    if _SERVICE_RE.search(text):
        return "service", None, "auto"

    # Project patterns
    if _PROJECT_RE.search(text):
        role = _infer_project_role(title)
        return "project", role, "auto"

    # Work patterns
    if _WORK_RE.search(text):
        return "work", None, "auto"

    # Subscription patterns
    if _SUBSCRIPTION_RE.search(text):
        return "subscription", None, "auto"

    # Personal patterns
    if _PERSONAL_RE.search(text):
        return "personal", None, "auto"

    # Groups that don't match anything → needs review
    return "personal", None, "needs_review"


def _infer_project_role(title: str) -> ProjectRole:
    """Infer the conversation's role in a project."""
    if re.search(r"甲方|业主|客户|大客户", title, re.IGNORECASE):
        return "client"
    if re.search(r"供应商|分包|厂家|材料|设备|施工方", title, re.IGNORECASE):
        return "vendor"
    if re.search(r"内部|核心|设计组|方案组|项目组", title, re.IGNORECASE):
        return "core"
    return "collab"


def classify_all(
    connection: sqlite3.Connection,
    *,
    only_needs_review: bool = True,
) -> ClassificationResult:
    """Classify all conversations in the database.

    Parameters
    ----------
    only_needs_review:
        When True, only re-classify conversations with classification_status
        = 'needs_review' or NULL.  When False, re-classify everything.
    """
    now_ms = int(time.time() * 1000)

    if only_needs_review:
        rows = connection.execute(
            """
            SELECT id, title, kind FROM conversations
            WHERE classification_status IS NULL
               OR classification_status = 'needs_review'
            """
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT id, title, kind FROM conversations"
        ).fetchall()

    by_domain: dict[str, int] = {}
    classified = 0
    needs_review = 0

    for row in rows:
        conv_id = row["id"]
        title = row["title"] or ""
        kind = row["kind"]

        # Sample a few recent messages for content-based classification
        sample = connection.execute(
            """
            SELECT content FROM messages
            WHERE conversation_id = ? AND content IS NOT NULL
            ORDER BY sent_at DESC LIMIT 3
            """,
            (conv_id,),
        ).fetchall()
        sample_text = " ".join(r["content"] or "" for r in sample)[:500]

        domain, role, status = classify_conversation(
            title, kind, sample_content=sample_text or None,
        )

        connection.execute(
            """
            UPDATE conversations
            SET domain = ?, project_id = NULL, project_role = ?,
                classification_status = ?, classified_at = ?
            WHERE id = ?
            """,
            (domain, role, status, now_ms, conv_id),
        )

        by_domain[domain] = by_domain.get(domain, 0) + 1
        if status == "auto":
            classified += 1
        else:
            needs_review += 1

    connection.commit()

    result = ClassificationResult(
        evaluated=len(rows),
        classified=classified,
        needs_review=needs_review,
        by_domain=by_domain,
    )
    logger.info(
        "conversation classification: evaluated=%d classified=%d needs_review=%d domains=%s",
        result.evaluated, result.classified, result.needs_review,
        result.by_domain,
    )
    return result
