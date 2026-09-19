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

_DIRECT_WORK_PATTERNS = [
    *_PROJECT_PATTERNS, *_WORK_PATTERNS,
    r"模型", r"数据", r"系统", r"网站", r"前端", r"后端",
    r"开发", r"审批", r"业务", r"客户", r"合同", r"交付", r"图纸",
]

_DIRECT_PERSONAL_PATTERNS = [
    *_PERSONAL_PATTERNS,
    r"爸爸", r"妈妈", r"父母", r"老婆", r"老公", r"孩子", r"宝宝",
    r"哥哥", r"姐姐", r"弟弟", r"妹妹", r"吾儿", r"家里", r"回家",
    r"吃饭", r"睡觉", r"聚会",
]

# Compile patterns
_PROJECT_RE = re.compile("|".join(_PROJECT_PATTERNS), re.IGNORECASE)
_WORK_RE = re.compile("|".join(_WORK_PATTERNS), re.IGNORECASE)
_SERVICE_RE = re.compile("|".join(_SERVICE_PATTERNS), re.IGNORECASE)
_SUBSCRIPTION_RE = re.compile("|".join(_SUBSCRIPTION_PATTERNS), re.IGNORECASE)
_PERSONAL_RE = re.compile("|".join(_PERSONAL_PATTERNS), re.IGNORECASE)
_DIRECT_WORK_RE = re.compile("|".join(_DIRECT_WORK_PATTERNS), re.IGNORECASE)
_DIRECT_PERSONAL_RE = re.compile("|".join(_DIRECT_PERSONAL_PATTERNS), re.IGNORECASE)


def _signal_score(pattern: re.Pattern[str], title: str, content: str) -> int:
    return (8 if pattern.search(title) else 0) + len(pattern.findall(content))


def _classify_direct(title: str, sample_content: str) -> tuple[Domain, str]:
    scores = (
        ("service", _signal_score(_SERVICE_RE, title, sample_content)),
        ("subscription", _signal_score(_SUBSCRIPTION_RE, title, sample_content)),
        ("work", _signal_score(_DIRECT_WORK_RE, title, sample_content)),
        ("personal", _signal_score(_DIRECT_PERSONAL_RE, title, sample_content)),
    )
    domain, score = max(scores, key=lambda item: item[1])
    return (domain, "auto") if score >= 2 else ("personal", "needs_review")


def _sample_recent_content(connection: sqlite3.Connection, conversation_id: str) -> str:
    rows = connection.execute(
        """
        SELECT content FROM messages
        WHERE conversation_id = ? AND type = 'text'
          AND content IS NOT NULL AND length(trim(content)) > 1
        ORDER BY sent_at DESC LIMIT 20
        """,
        (conversation_id,),
    ).fetchall()
    return " ".join(row["content"] or "" for row in rows)[:4000]


def classify_conversation(
    title: str,
    kind: str,
    *,
    sample_content: str | None = None,
) -> tuple[Domain, ProjectRole | None, str]:
    """Classify a single conversation.

    Returns (domain, project_role, classification_status).
    """
    content = sample_content or ""
    text = f"{title} {content}"

    # System/official accounts → subscription
    if kind == "system":
        return "subscription", None, "auto"

    # Direct chats keep the existing domain taxonomy, but infer the relationship
    # from repeated title/content signals instead of defaulting every contact to
    # personal. Project language maps to work because project is group-only.
    if kind == "direct":
        domain, status = _classify_direct(title, content)
        return domain, None, status

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

        sample_text = _sample_recent_content(connection, conv_id)

        domain, role, status = classify_conversation(
            title, kind, sample_content=sample_text or None,
        )

        # Write derived classification to conversation_overrides, never the
        # raw conversations table.  Reads COALESCE override over base.
        connection.execute(
            """
            INSERT INTO conversation_overrides
                (conversation_id, domain, project_id, project_role,
                 classification_status, classified_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(conversation_id) DO UPDATE SET
                domain = excluded.domain,
                project_id = NULL,
                project_role = excluded.project_role,
                classification_status = excluded.classification_status,
                classified_at = excluded.classified_at,
                updated_at = excluded.updated_at
            """,
            (conv_id, domain, role, status, now_ms),
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


def classify_all_overrides(
    connection: sqlite3.Connection,
    *,
    only_needs_review: bool = True,
) -> ClassificationResult:
    """Same as classify_all but writes to conversation_overrides,
    never touches the raw conversations table.
    """
    now_ms = int(time.time() * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if only_needs_review:
        rows = connection.execute(
            """
            SELECT c.id, c.title, c.kind,
                   COALESCE(o.classification_status, c.classification_status, 'needs_review') AS status
            FROM conversations c
            LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
            WHERE COALESCE(o.classification_status, c.classification_status, 'needs_review') IS NULL
               OR COALESCE(o.classification_status, c.classification_status, 'needs_review') = 'needs_review'
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT c.id, c.title, c.kind,
                   COALESCE(o.classification_status, c.classification_status, 'needs_review') AS status
            FROM conversations c
            LEFT JOIN conversation_overrides o ON o.conversation_id = c.id
            """
        ).fetchall()

    by_domain: dict[str, int] = {}
    classified = 0
    needs_review = 0

    for row in rows:
        conv_id = row["id"]
        title = row["title"] or ""
        kind = row["kind"]

        sample_text = _sample_recent_content(connection, conv_id)

        domain, role, status = classify_conversation(
            title, kind, sample_content=sample_text or None,
        )

        connection.execute(
            """
            INSERT INTO conversation_overrides
                (conversation_id, domain, project_role, classification_status, classified_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                domain = excluded.domain,
                project_role = excluded.project_role,
                classification_status = excluded.classification_status,
                classified_at = excluded.classified_at,
                updated_at = excluded.updated_at
            """,
            (conv_id, domain, role, status, now_ms, now_iso),
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
        "classification overrides: evaluated=%d classified=%d needs_review=%d",
        result.evaluated, result.classified, result.needs_review,
    )
    return result
