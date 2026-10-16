#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构造 verify-entry 库 schema 契约的最小测试库（CI 用）。

用法: python scripts/make-ci-library.py <输出路径>

表与列严格对应 scripts/entry-contract.mjs 中 validateLibraryDb 的 required 契约，
只建表不填数据 —— CI 里 verify-entry --preflight 只需验证 schema 契约成立。
"""
import os
import sqlite3
import sys

SCHEMA = {
    "app_meta": "key TEXT PRIMARY KEY, value TEXT",
    "conversations": (
        "id TEXT PRIMARY KEY, title TEXT, kind TEXT, message_count INTEGER, "
        "last_message_at INTEGER, is_favorite INTEGER, is_indexing_enabled INTEGER, "
        "domain TEXT, project_id TEXT, project_role TEXT"
    ),
    "messages": (
        "id INTEGER PRIMARY KEY, conversation_id TEXT, sender_id TEXT, "
        "sender_name TEXT, sent_at INTEGER, type TEXT, content TEXT"
    ),
    "message_parts": "id INTEGER PRIMARY KEY",
    "assets": "id INTEGER PRIMARY KEY",
    "tasks": (
        "task_id TEXT PRIMARY KEY, conversation_id TEXT, content TEXT, "
        "sender_name TEXT, sent_at INTEGER, due_date INTEGER, priority TEXT, "
        "status TEXT, updated_at INTEGER"
    ),
}


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = sys.argv[1]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    conn = sqlite3.connect(out)
    try:
        for table, columns in SCHEMA.items():
            conn.execute(f"CREATE TABLE {table} ({columns})")
        conn.commit()
    finally:
        conn.close()
    print(f"OK 已建 schema 契约库: {out} ({len(SCHEMA)} 张表)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
