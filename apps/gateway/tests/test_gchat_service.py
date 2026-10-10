from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


def _seed_database(path: Path) -> sqlite3.Connection:
    from wechat_ai.database import initialize_database

    connection = initialize_database(path)
    connection.execute(
        """
        INSERT INTO conversations(
            id, source_conversation_id, title, kind,
            last_message_at, message_count
        ) VALUES ('project-room', 'project-room', 'Project Room', 'group', 2000, 1)
        """
    )
    cursor = connection.execute(
        """
        INSERT INTO messages(
            conversation_id, source_message_id, dedupe_key, sender_id,
            sender_name, sent_at, type, content
        ) VALUES (
            'project-room', 'source-1', 'dedupe-1', 'alice',
            'Alice', 2000, 'text', 'launch checklist is ready'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO message_parts(message_id, ordinal, kind, status, text_content)
        VALUES (?, 0, 'text', 'resolved', 'launch checklist is ready')
        """,
        (cursor.lastrowid,),
    )
    connection.commit()
    return connection


def test_projects_assign_conversations_and_build_a_pulse(tmp_path: Path) -> None:
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)) as connection:
        service = GchatService(lambda: sqlite3.connect(database_path))
        project = service.create_project("Launch", "Release work")
        service.assign_conversation(project["id"], "project-room", source="user")

        listed = service.list_projects()
        pulse = service.get_project_pulse(project["id"])

    assert listed["items"][0]["name"] == "Launch"
    assert listed["items"][0]["conversationCount"] == 1
    assert pulse["projectId"] == project["id"]
    assert pulse["messageCount"] == 1
    assert pulse["recentMessages"][0]["excerpt"] == "launch checklist is ready"


def test_structured_project_assignment_extractor_creates_idempotent_suggestion(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "project-candidate.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id, part_id = connection.execute(
            """
            SELECT messages.id, message_parts.id
            FROM messages
            JOIN message_parts ON message_parts.message_id = messages.id
            """
        ).fetchone()
    extractor_calls: list[dict[str, object]] = []

    def extractor(payload: dict[str, object]):
        extractor_calls.append(payload)
        return [
            {
                "conversationId": "project-room",
                "suggestedProjectName": "Launch",
                "confidence": 0.9,
                "evidence": [
                    {"messageId": message_id, "partId": part_id}
                ],
            }
        ]

    service = GchatService(
        lambda: sqlite3.connect(database_path),
        clock=lambda: 7_000,
        project_assignment_extractor=extractor,
    )
    request = {
        "question": "Which project?",
        "answer": "This belongs to Launch.",
        "scope": {"mode": "global"},
        "citations": [
            {
                "conversationId": "project-room",
                "messageId": message_id,
                "partId": part_id,
            }
        ],
        "source_turn_id": "turn-1",
        "model_profile_id": "local-default",
    }

    first = service.generate_insights(**request)
    duplicate = service.generate_insights(**request)

    assert len(extractor_calls) == 2
    assert duplicate == first
    assert first[0]["status"] == "suggested"
    assert first[0]["suggestedProjectName"] == "Launch"
    assert first[0]["evidence"] == [
        {
            "conversationId": "project-room",
            "messageId": int(message_id),
            "partId": int(part_id),
            "senderName": "Alice",
            "sentAt": 2_000,
            "excerpt": "launch checklist is ready",
        }
    ]
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM project_assignment_candidates"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM project_conversations"
        ).fetchone()[0] == 0


def test_archive_import_suggests_project_assignment_without_moving_conversation(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "archive-project-candidate.sqlite"
    with closing(_seed_database(database_path)) as connection:
        service = GchatService(
            lambda: sqlite3.connect(database_path),
            clock=lambda: 8_000,
        )
        project = service.create_project("Launch")

    created = service.suggest_project_assignments_from_archive()
    duplicate = service.suggest_project_assignments_from_archive()

    assert len(created) == 1
    assert created[0]["suggestedProjectId"] == project["id"]
    assert created[0]["suggestedProjectName"] == "Launch"
    assert created[0]["status"] == "suggested"
    assert duplicate == []
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM project_conversations"
        ).fetchone()[0] == 0


def test_memory_lifecycle_keeps_message_evidence_and_requires_confirmation(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.gchat_service import GchatService, GchatValidationError

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(lambda: sqlite3.connect(database_path))
        memory = service.create_memory(
            content="发布前需要完成 launch checklist",
            memory_type="constraint",
            scope={"kind": "conversation", "id": "project-room"},
            evidence=[{"messageId": message_id}],
            status="suggested",
        )
        duplicate = service.create_memory(
            content="发布前需要完成 launch checklist",
            memory_type="constraint",
            scope={"kind": "conversation", "id": "project-room"},
            evidence=[{"messageId": message_id}],
            status="suggested",
        )
        assert memory["status"] == "suggested"
        assert duplicate["id"] == memory["id"]
        assert duplicate["evidenceCount"] == 1
        assert service.confirmed_memories_for_scope(
            {"mode": "selection", "conversationIds": ["project-room"]}
        ) == {"items": []}

        edited = service.update_memory(
            memory["id"],
            content="发布前需要完成最终 launch checklist",
        )
        assert edited["content"] == "发布前需要完成最终 launch checklist"
        confirmed = service.update_memory(memory["id"], status="confirmed")
        confirmed_again = service.update_memory(
            memory["id"],
            status="confirmed",
        )
        evidence = service.list_memory_evidence(memory["id"])

    assert confirmed["status"] == "confirmed"
    assert confirmed_again["id"] == memory["id"]
    assert evidence["items"][0]["messageId"] == message_id
    assert evidence["items"][0]["conversationId"] == "project-room"
    assert service.confirmed_memories_for_scope(
        {"mode": "selection", "conversationIds": ["project-room"]}
    )["items"][0]["id"] == memory["id"]
    with pytest.raises(GchatValidationError):
        service.update_memory(memory["id"], status="rejected")


def test_capture_turn_only_creates_a_candidate_for_an_explicit_memory_command(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(lambda: sqlite3.connect(database_path))
        project = service.create_project("Launch")
        service.assign_conversation(project["id"], "project-room")
        created = service.capture_turn(
            question="请记住：发布前必须完成验收",
            answer="已记录为待确认记忆。",
            scope={
                "mode": "project",
                "projectId": project["id"],
                "conversationIds": ["project-room"],
            },
            citations=[{"messageId": message_id, "partId": 1}],
        )
        duplicate = service.capture_turn(
            question="请记住：发布前必须完成验收",
            answer="已记录为待确认记忆。",
            scope={
                "mode": "project",
                "projectId": project["id"],
                "conversationIds": ["project-room"],
            },
            citations=[{"messageId": message_id, "partId": 1}],
        )
        service.unassign_conversation(project["id"], "project-room")
        after_membership_change = service.capture_turn(
            question="请记住：快照内证据仍属于原项目 turn",
            answer="已记录为待确认记忆。",
            scope={
                "mode": "project",
                "projectId": project["id"],
                "conversationIds": ["project-room"],
            },
            citations=[{"messageId": message_id, "partId": 1}],
        )
        conversation_scoped = service.capture_turn(
            question="请记住：这是当前会话约束",
            answer="已记录为待确认记忆。",
            scope={"mode": "selection", "conversationIds": ["project-room"]},
            citations=[{"messageId": message_id, "partId": 1}],
        )
        without_evidence = service.capture_turn(
            question="请记住：这条没有来源",
            answer="不能记录。",
            scope={"mode": "selection", "conversationIds": ["project-room"]},
            citations=[],
        )

    assert created is not None
    assert created["status"] == "suggested"
    assert created["content"] == "发布前必须完成验收"
    assert created["scope"] == {"kind": "project", "id": project["id"]}
    assert duplicate is not None
    assert duplicate["id"] == created["id"]
    assert after_membership_change is not None
    assert after_membership_change["scope"] == {
        "kind": "project",
        "id": project["id"],
    }
    assert conversation_scoped is not None
    assert conversation_scoped["scope"] == {
        "kind": "conversation",
        "id": "project-room",
    }
    assert without_evidence is None


def test_proposals_reject_missing_evidence_and_active_creation(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.gchat_service import GchatService, GchatValidationError

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)):
        service = GchatService(lambda: sqlite3.connect(database_path))
        with pytest.raises(GchatValidationError):
            service.create_memory(
                content="没有来源的记忆",
                memory_type="fact",
                scope={"kind": "global", "id": ""},
            )
        with pytest.raises(GchatValidationError):
            service.create_memory(
                content="跳过确认的记忆",
                memory_type="fact",
                scope={"kind": "global", "id": ""},
                evidence=[{"messageId": 1, "partId": 1}],
                status="confirmed",
            )
        with pytest.raises(GchatValidationError):
            service.create_decision(
                title="没有来源",
                decision="不能创建",
            )
        with pytest.raises(GchatValidationError):
            service.create_decision(
                title="跳过确认",
                decision="不能直接生效",
                evidence=[{"messageId": 1, "partId": 1}],
                status="accepted",
            )


def test_decision_can_be_promoted_from_proposed_to_accepted(tmp_path: Path) -> None:
    from wechat_ai.services.gchat_service import GchatService, GchatValidationError

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(lambda: sqlite3.connect(database_path))
        decision = service.create_decision(
            title="发布路径",
            decision="先完成本地验收再发布",
            project_id=None,
            next_action="准备验收清单",
            evidence=[{"messageId": message_id, "partId": 1}],
        )
        duplicate = service.create_decision(
            title="发布路径",
            decision="先完成本地验收再发布",
            project_id=None,
            next_action="准备验收清单",
            evidence=[{"messageId": message_id, "partId": 1}],
        )
        edited = service.update_decision(
            decision["id"],
            decision="先完成最终本地验收再发布",
        )
        accepted = service.update_decision(decision["id"], status="accepted")
        accepted_again = service.update_decision(
            decision["id"],
            status="accepted",
        )
        evidence = service.list_decision_evidence(decision["id"])

    assert decision["status"] == "proposed"
    assert duplicate["id"] == decision["id"]
    assert edited["decision"] == "先完成最终本地验收再发布"
    assert accepted["status"] == "accepted"
    assert accepted_again["id"] == decision["id"]
    assert evidence["items"][0]["conversationId"] == "project-room"
    with pytest.raises(GchatValidationError):
        service.update_decision(decision["id"], status="rejected")


def test_rejected_proposals_are_terminal_and_idempotent(tmp_path: Path) -> None:
    from wechat_ai.services.gchat_service import GchatService, GchatValidationError

    database_path = tmp_path / "library.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(lambda: sqlite3.connect(database_path))
        memory = service.create_memory(
            content="拒绝这个候选记忆",
            memory_type="fact",
            scope={"kind": "conversation", "id": "project-room"},
            evidence=[{"messageId": message_id, "partId": 1}],
        )
        rejected_memory = service.update_memory(
            memory["id"],
            status="rejected",
        )
        repeated_memory = service.update_memory(
            memory["id"],
            status="rejected",
        )
        decision = service.create_decision(
            title="拒绝的决定",
            decision="不采用这个方案",
            evidence=[{"messageId": message_id, "partId": 1}],
        )
        rejected_decision = service.update_decision(
            decision["id"],
            status="rejected",
        )
        repeated_decision = service.update_decision(
            decision["id"],
            status="rejected",
        )

    assert rejected_memory["status"] == "rejected"
    assert repeated_memory["id"] == memory["id"]
    assert rejected_decision["status"] == "rejected"
    assert repeated_decision["id"] == decision["id"]
    with pytest.raises(GchatValidationError):
        service.update_memory(memory["id"], status="confirmed")
    with pytest.raises(GchatValidationError):
        service.update_decision(decision["id"], status="accepted")


def test_memory_fact_evolution_supersedes_old_active_memory_without_deleting(
    tmp_path: Path,
) -> None:
    """新事实进来，同一主体+同一 subject_key+同一 memory_type 的旧记忆被标记
    superseded（保留原记录），新记忆为 suggested；list_memories 默认不返回
    superseded，include_superseded=True 才可见。绝不物理删除。
    """
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "fact-evolution.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(
            lambda: sqlite3.connect(database_path),
            clock=lambda: 9_000,
        )

        # 1) 写入第一条事实（旧记忆）
        old = service.create_memory(
            content="办公室地址在 A 座 3 层",
            memory_type="fact",
            scope={"kind": "global", "id": ""},
            evidence=[{"messageId": message_id, "partId": 1}],
            subject_key="office.address",
        )
        assert old["status"] == "suggested"
        assert old["subjectKey"] == "office.address"
        assert old["supersededBy"] is None

        # 确认一下，让它变成 confirmed（active 集合）
        confirmed_old = service.update_memory(old["id"], status="confirmed")
        assert confirmed_old["status"] == "confirmed"

        # 2) 写入第二条事实（新事实，同一 subject_key）
        service._clock = lambda: 10_000
        new = service.create_memory(
            content="办公室地址搬到 B 座 12 层",
            memory_type="fact",
            scope={"kind": "global", "id": ""},
            evidence=[{"messageId": message_id, "partId": 1}],
            subject_key="office.address",
        )
        assert new["status"] == "suggested"
        assert new["id"] != old["id"]

        # 3) 旧记录仍在库里（绝不物理删除），状态翻为 superseded，且 superseded_by 指向新记忆
        with closing(sqlite3.connect(database_path)) as conn2:
            conn2.row_factory = sqlite3.Row
            row = conn2.execute(
                "SELECT status, superseded_by FROM gchat_memories WHERE id = ?",
                (old["id"],),
            ).fetchone()
            assert row is not None
            assert row["status"] == "superseded"
            assert row["superseded_by"] == new["id"]
            # 新记录也在
            assert conn2.execute(
                "SELECT 1 FROM gchat_memories WHERE id = ? AND status = 'suggested'",
                (new["id"],),
            ).fetchone() is not None

    # 4) 可见性：默认 list_memories 不返回 superseded
    visible = service.list_memories()
    visible_ids = {item["id"] for item in visible["items"]}
    assert new["id"] in visible_ids
    assert old["id"] not in visible_ids

    # 5) include_superseded=True 能看到旧记录
    all_items = service.list_memories(include_superseded=True)
    all_ids = {item["id"] for item in all_items["items"]}
    assert old["id"] in all_ids
    superseded_item = next(
        item for item in all_items["items"] if item["id"] == old["id"]
    )
    assert superseded_item["status"] == "superseded"
    assert superseded_item["supersededBy"] == new["id"]

    # 6) confirmed_memories_for_scope 自然不返回 superseded（它本来就只查 confirmed）
    confirmed = service.confirmed_memories_for_scope({"mode": "global"})
    confirmed_ids = {item["id"] for item in confirmed["items"]}
    assert old["id"] not in confirmed_ids


def test_memory_fact_evolution_no_subject_key_means_no_supersession(
    tmp_path: Path,
) -> None:
    """subject_key 留空时，同类记忆互不取代（向后兼容）。"""
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "no-supersede.sqlite"
    with closing(_seed_database(database_path)) as connection:
        message_id = int(
            connection.execute("SELECT id FROM messages").fetchone()[0]
        )
        service = GchatService(lambda: sqlite3.connect(database_path))
        first = service.create_memory(
            content="随便一条事实",
            memory_type="fact",
            scope={"kind": "global", "id": ""},
            evidence=[{"messageId": message_id, "partId": 1}],
        )
        service.update_memory(first["id"], status="confirmed")
        second = service.create_memory(
            content="另一条事实",
            memory_type="fact",
            scope={"kind": "global", "id": ""},
            evidence=[{"messageId": message_id, "partId": 1}],
        )

    listed = service.list_memories()
    ids = {item["id"] for item in listed["items"]}
    assert first["id"] in ids
    assert second["id"] in ids
