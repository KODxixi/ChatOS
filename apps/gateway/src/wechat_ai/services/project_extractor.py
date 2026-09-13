"""Project information extraction for ArchOS bridge.

Scans project-domain conversations, extracts key information (design decisions,
milestones, client feedback), and generates project-brief.md files for
syncing to ArchOS.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ProjectInfo:
    project_id: str
    project_name: str
    conversation_ids: list[str] = field(default_factory=list)
    message_count: int = 0
    decisions: list[tuple[int, str, str]] = field(default_factory=list)  # (timestamp, speaker, text)
    milestones: list[tuple[int, str]] = field(default_factory=list)  # (timestamp, text)
    client_feedback: list[tuple[int, str, str]] = field(default_factory=list)
    last_updated: int = 0


# Patterns for extraction
_DECISION_PATTERNS = [
    r"决定", r"确定", r"定了", r"就按", r"最终方案", r"确认", r"敲定",
    r"通过", r"同意", r"采纳", r"改为", r"调整为",
]
_MILESTONE_PATTERNS = [
    r"报审", r"报建", r"施工", r"开工", r"竣工", r"交付", r"开盘",
    r"节点", r"截止", r"deadline", r"下周", r"本月", r"季度",
]
_CLIENT_PATTERNS = [
    r"甲方", r"业主", r"客户", r"开发商", r"要求", r"意见", r"反馈",
    r"不满意", r"要改", r"修改", r"调整",
]

_DECISION_RE = re.compile("|".join(_DECISION_PATTERNS), re.IGNORECASE)
_MILESTONE_RE = re.compile("|".join(_MILESTONE_PATTERNS), re.IGNORECASE)
_CLIENT_RE = re.compile("|".join(_CLIENT_PATTERNS), re.IGNORECASE)

# Project name normalization
_PROJECT_CLEAN_PATTERNS = [
    r"群$", r"项目群$", r"沟通群$", r"工作群$", r"讨论群$",
    r"^\d+", r"^[（(].*?[）)]",
]


def normalize_project_name(title: str) -> str:
    """Extract a clean project name from a conversation title."""
    name = title
    for pat in _PROJECT_CLEAN_PATTERNS:
        name = re.sub(pat, "", name).strip()
    return name or title


def project_id_from_name(name: str) -> str:
    """Generate a stable project ID from a project name."""
    import hashlib
    return "proj-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def extract_projects(
    connection: sqlite3.Connection,
    *,
    vault_root: Path,
    min_messages: int = 10,
) -> list[ProjectInfo]:
    """Extract project information from project-domain conversations.

    Returns a list of ProjectInfo objects and writes project-brief.md files
    to vault_root/conversations/project/{project_id}/.
    """
    now_ms = int(time.time() * 1000)

    # Get all project-domain conversations
    convs = connection.execute(
        """
        SELECT id, title, message_count
        FROM conversations
        WHERE domain = 'project'
          AND is_indexing_enabled = 1
          AND message_count >= ?
        ORDER BY message_count DESC
        """,
        (min_messages,),
    ).fetchall()

    # Group by project name
    projects: dict[str, ProjectInfo] = {}
    for conv in convs:
        name = normalize_project_name(conv["title"] or conv["id"])
        pid = project_id_from_name(name)
        if pid not in projects:
            projects[pid] = ProjectInfo(
                project_id=pid,
                project_name=name,
            )
        projects[pid].conversation_ids.append(conv["id"])
        projects[pid].message_count += conv["message_count"] or 0

    # Extract messages for each project
    for pid, info in projects.items():
        placeholders = ", ".join("?" for _ in info.conversation_ids)
        messages = connection.execute(
            f"""
            SELECT sent_at, sender_name, content
            FROM messages
            WHERE conversation_id IN ({placeholders})
              AND content IS NOT NULL
              AND length(content) > 5
            ORDER BY sent_at DESC
            LIMIT 500
            """,
            info.conversation_ids,
        ).fetchall()

        for msg in messages:
            content = msg["content"] or ""
            sender = msg["sender_name"] or "未知"
            ts = msg["sent_at"] or 0

            if _DECISION_RE.search(content):
                info.decisions.append((ts, sender, content[:200]))
            if _MILESTONE_RE.search(content):
                info.milestones.append((ts, content[:200]))
            if _CLIENT_RE.search(content):
                info.client_feedback.append((ts, sender, content[:200]))

        info.last_updated = now_ms

        # Write project-brief.md
        _write_project_brief(vault_root, info)

    logger.info(
        "project extraction: %d projects from %d conversations",
        len(projects), len(convs),
    )
    return list(projects.values())


def _write_project_brief(vault_root: Path, info: ProjectInfo) -> None:
    """Write a project-brief.md file for the project."""
    project_dir = vault_root / "conversations" / "project" / info.project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    brief_path = project_dir / "project-brief.md"

    lines = [
        f"# {info.project_name}",
        "",
        "## 基本信息",
        f"- 项目 ID: `{info.project_id}`",
        f"- 关联会话数: {len(info.conversation_ids)}",
        f"- 消息总数: {info.message_count}",
        f"- 最后更新: {time.strftime('%Y-%m-%d %H:%M', time.localtime(info.last_updated / 1000))}",
        "",
    ]

    if info.decisions:
        lines.append("## 设计决策")
        for ts, sender, text in sorted(info.decisions, key=lambda x: x[0], reverse=True)[:15]:
            date = time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
            lines.append(f"- **{date}** ({sender}): {text}")
        lines.append("")

    if info.milestones:
        lines.append("## 时间节点")
        for ts, text in sorted(info.milestones, key=lambda x: x[0], reverse=True)[:10]:
            date = time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
            lines.append(f"- **{date}**: {text}")
        lines.append("")

    if info.client_feedback:
        lines.append("## 甲方意见")
        for ts, sender, text in sorted(info.client_feedback, key=lambda x: x[0], reverse=True)[:10]:
            date = time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
            lines.append(f"- **{date}** ({sender}): {text}")
        lines.append("")

    lines.append("---")
    lines.append("*由 ChatOS 项目提取 Agent 自动生成，同步至 ArchOS*")

    brief_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote project brief: %s (%d decisions, %d milestones, %d feedback)",
                brief_path, len(info.decisions), len(info.milestones), len(info.client_feedback))
