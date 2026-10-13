from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _settings(tmp_path: Path):
    from wechat_ai.config import Settings

    return Settings(
        project_root=tmp_path,
        source_dir=tmp_path / "decrypted",
        library_db=tmp_path / "data" / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
    )


def test_app_exposes_gchat_project_memory_and_decision_routes(tmp_path: Path) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    paths = app.openapi()["paths"]

    assert "/api/projects" in paths
    assert "/api/projects/{project_id}/pulse" in paths
    assert "/api/gchat/memories" in paths
    assert "/api/gchat/memories/{memory_id}" in paths
    assert "/api/gchat/decisions" in paths
    assert "/api/gchat/decisions/{decision_id}/evidence" in paths
    assert "/api/gchat/preset" in paths


def test_gchat_preset_is_generic_and_deterministic(tmp_path: Path) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/gchat/preset")
        second = client.get("/api/gchat/preset")

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["id"] == "gchat"
    assert payload["name"] == "Gchat"
    assert payload["content"]
    assert "## Core Truths" in payload["content"]
    assert "## 6 件事" in payload["content"]
    assert "## /acp dispatcher" in payload["content"]
    assert "## Active mode" in payload["content"]
    assert "## Messenger red lines" in payload["content"]
    assert "## Continuity" in payload["content"]
    assert "## Local WeChat RAG answer mode" in payload["content"]
    assert "embedding 模型" in payload["content"]
    assert "reranker 模型" in payload["content"]
    assert "以时间最晚的对话为准" in payload["content"]
    assert "根据本地微信记录，未查找到相关讨论。" in payload["content"]

    # The product preset must not leak a user's private names, paths, or ports.
    for private_token in (
        "G爸",
        "Gaxis",
        "Gstok",
        "C:\\",
        "D:\\",
        "127.0.0.1",
        "8912",
        "8765",
    ):
        assert private_token not in payload["content"]


def test_project_api_can_create_and_read_a_project(tmp_path: Path) -> None:
    from wechat_ai.app import create_app

    app = create_app(
        _settings(tmp_path),
        source_probe=lambda: "missing",
        lm_probe=lambda: "offline",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/api/health")
        created = client.post(
            "/api/projects",
            json={"name": "Launch", "description": "Release work"},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        listed = client.get("/api/projects")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == project_id


def test_proposal_api_enforces_review_and_exposes_navigable_evidence(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.gchat import create_gchat_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "gchat-api.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(id, source_conversation_id, title, kind)
            VALUES ('project-room', 'project-room', 'Project Room', 'group')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('project-room', 'message-1', 'Alice',
                      2000, 'text', 'launch checklist is ready')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'launch checklist is ready')
            """,
            (message.lastrowid,),
        )
        connection.commit()
    finally:
        connection.close()

    app = FastAPI()
    app.include_router(
        create_gchat_router(
            GchatService(create_connection_factory(database_path))
        ),
        prefix="/api",
    )
    client = TestClient(app)
    memory_payload = {
        "content": "发布前完成验收",
        "memoryType": "constraint",
        "scope": {"kind": "conversation", "id": "project-room"},
        "evidence": [
            {"messageId": message.lastrowid, "partId": part.lastrowid}
        ],
    }

    bypass = client.post(
        "/api/gchat/memories",
        json={**memory_payload, "status": "confirmed"},
    )
    assert bypass.status_code == 422
    created = client.post("/api/gchat/memories", json=memory_payload)
    duplicate = client.post("/api/gchat/memories", json=memory_payload)
    assert created.status_code == 201
    assert created.json()["status"] == "suggested"
    assert duplicate.json()["id"] == created.json()["id"]

    confirmed = client.patch(
        f"/api/gchat/memories/{created.json()['id']}",
        json={"status": "confirmed"},
    )
    assert confirmed.status_code == 200
    rejected_after_confirm = client.patch(
        f"/api/gchat/memories/{created.json()['id']}",
        json={"status": "rejected"},
    )
    assert rejected_after_confirm.status_code == 422

    decision_payload = {
        "title": "发布路径",
        "decision": "先验收再发布",
        "evidence": [
            {"messageId": message.lastrowid, "partId": part.lastrowid}
        ],
    }
    decision_bypass = client.post(
        "/api/gchat/decisions",
        json={**decision_payload, "status": "accepted"},
    )
    assert decision_bypass.status_code == 422
    decision = client.post("/api/gchat/decisions", json=decision_payload)
    assert decision.status_code == 201
    assert decision.json()["status"] == "proposed"
    decision_evidence = client.get(
        f"/api/gchat/decisions/{decision.json()['id']}/evidence"
    )
    assert decision_evidence.status_code == 200
    assert decision_evidence.json()["items"][0]["conversationId"] == "project-room"


def test_project_assignment_candidate_api_requires_review_before_assignment(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.gchat import create_gchat_router
    from wechat_ai.database import initialize_database
    from wechat_ai.services.conversation_service import create_connection_factory
    from wechat_ai.services.gchat_service import GchatService

    database_path = tmp_path / "project-candidate-api.sqlite"
    connection = initialize_database(database_path)
    try:
        connection.execute(
            """
            INSERT INTO conversations(id, source_conversation_id, title, kind)
            VALUES ('project-room', 'project-room', 'Project Room', 'group')
            """
        )
        message = connection.execute(
            """
            INSERT INTO messages(
                conversation_id, dedupe_key, sender_name,
                sent_at, type, content
            ) VALUES ('project-room', 'candidate-message', 'Alice',
                      2000, 'text', 'launch checklist is ready')
            """
        )
        part = connection.execute(
            """
            INSERT INTO message_parts(
                message_id, ordinal, kind, status, text_content
            ) VALUES (?, 0, 'text', 'resolved', 'launch checklist is ready')
            """,
            (message.lastrowid,),
        )
        connection.commit()
    finally:
        connection.close()

    suggestions = iter(("Launch", "Rejected Project"))

    def extractor(_payload):
        return [
            {
                "conversationId": "project-room",
                "suggestedProjectName": next(suggestions),
                "confidence": 0.9,
                "evidence": [
                    {
                        "messageId": message.lastrowid,
                        "partId": part.lastrowid,
                    }
                ],
            }
        ]

    service = GchatService(
        create_connection_factory(database_path),
        project_assignment_extractor=extractor,
        clock=lambda: 8_000,
    )
    common = {
        "question": "Classify",
        "answer": "Candidate",
        "scope": {"mode": "global"},
        "citations": [
            {
                "conversationId": "project-room",
                "messageId": message.lastrowid,
                "partId": part.lastrowid,
            }
        ],
        "model_profile_id": "local-default",
    }
    first = service.generate_insights(source_turn_id="turn-1", **common)[0]
    second = service.generate_insights(source_turn_id="turn-2", **common)[0]

    app = FastAPI()
    app.include_router(create_gchat_router(service), prefix="/api")
    client = TestClient(app)
    listed = client.get(
        "/api/gchat/project-assignment-candidates?status=suggested"
    )
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} == {
        first["id"],
        second["id"],
    }

    confirmed = client.post(
        f"/api/gchat/project-assignment-candidates/{first['id']}/confirm",
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    rejected = client.post(
        f"/api/gchat/project-assignment-candidates/{second['id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.post(
        f"/api/gchat/project-assignment-candidates/{first['id']}/reject"
    ).status_code == 422

    with closing(sqlite3.connect(database_path)) as connection:
        assignments = connection.execute(
            """
            SELECT projects.name, project_conversations.assignment_source
            FROM project_conversations
            JOIN projects ON projects.id = project_conversations.project_id
            """
        ).fetchall()
    assert assignments == [("Launch", "agent")]
