from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _make_services(
    tmp_path: Path,
    *,
    classifier=None,
):
    from wechat_ai.database import initialize_database
    from wechat_ai.services.agent_thread_service import AgentThreadService
    from wechat_ai.services.conversation_service import create_connection_factory

    database_path = tmp_path / "route.sqlite"
    initialize_database(database_path).close()
    connection_factory = create_connection_factory(database_path)
    alpha = {"id": "project-alpha", "name": "Alpha 项目"}
    beta = {"id": "project-beta", "name": "Beta 项目"}
    with connection_factory() as connection:
        connection.executemany(
            """
            INSERT INTO projects(id, name, description, status, created_at, updated_at)
            VALUES (?, ?, '', 'active', ?, ?)
            """,
            [(alpha["id"], alpha["name"], 1_000, 1_000),
             (beta["id"], beta["name"], 1_000, 1_000)],
        )
        connection.executemany(
            "INSERT INTO project_pulses(project_id, updated_at) VALUES (?, ?)",
            [(alpha["id"], 1_000), (beta["id"], 1_000)],
        )
        connection.commit()
    threads = AgentThreadService(connection_factory, clock=lambda: 1_000)
    thread = threads.create_thread(title="路由线程", scope={"mode": "global"})

    from wechat_ai.services.agent_route_service import AgentRouteService

    route_service = AgentRouteService(
        connection_factory,
        classifier=classifier or (lambda _question: '{"projectId": "", "confidence": 0}'),
        thread_id=thread["id"],
        clock=lambda: 2_000,
    )
    return route_service, connection_factory, thread, alpha, beta


def _candidate_row(connection_factory, candidate_id: str) -> sqlite3.Row:
    with connection_factory() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM agent_route_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    assert row is not None
    return row


def test_route_prefers_explicit_project_name_without_calling_classifier(
    tmp_path: Path,
) -> None:
    called = False

    def classifier(_question: str) -> str:
        nonlocal called
        called = True
        return '{"projectId": "wrong", "confidence": 0.99}'

    service, connection_factory, thread, alpha, _beta = _make_services(
        tmp_path,
        classifier=classifier,
    )

    routed = service.route("请查看 Alpha 项目的最新进展")

    assert called is False
    assert routed["scope"] == {"mode": "project", "projectId": alpha["id"]}
    assert routed["project"] == {"id": alpha["id"], "name": alpha["name"]}
    assert routed["candidate"]["routeSource"] == "explicit"
    assert routed["candidate"]["confidence"] == 1.0
    with connection_factory() as connection:
        assert connection.execute(
            "SELECT project_id FROM agent_threads WHERE id = ?",
            (thread["id"],),
        ).fetchone()[0] is None


def test_route_uses_high_confidence_existing_model_project_as_candidate(
    tmp_path: Path,
) -> None:
    service, _connection_factory, _thread, _alpha, beta = _make_services(
        tmp_path,
        classifier=lambda _question: (
            '{"projectId": "' + beta["id"] + '", "confidence": 0.86}'
        ),
    )

    routed = service.route("这件事更像第二个项目")

    assert routed["scope"] == {"mode": "project", "projectId": beta["id"]}
    assert routed["project"] == {"id": beta["id"], "name": beta["name"]}
    assert routed["candidate"]["status"] == "suggested"
    assert routed["candidate"]["routeSource"] == "model"


@pytest.mark.parametrize(
    ("model_output", "question"),
    [
        ('{"projectId": "project-alpha", "confidence": 0.74}', "低置信度"),
        ("not valid json", "模型输出损坏"),
        ('{"projectId": "missing", "confidence": 0.99}', "未知项目"),
    ],
)
def test_route_falls_back_to_global_for_unusable_model_output(
    tmp_path: Path,
    model_output: str,
    question: str,
) -> None:
    service, _connection_factory, _thread, _alpha, _beta = _make_services(
        tmp_path,
        classifier=lambda _question: model_output,
    )

    routed = service.route(question)

    assert routed == {
        "scope": {"mode": "global"},
        "project": None,
        "candidate": None,
    }
    assert service.list_candidates() == {"items": []}


def test_confirm_updates_thread_and_candidate_status(tmp_path: Path) -> None:
    service, connection_factory, thread, alpha, _beta = _make_services(
        tmp_path,
        classifier=lambda _question: (
            '{"projectId": "' + alpha["id"] + '", "confidence": 0.9}'
        ),
    )
    candidate = service.route("确认 Alpha 项目") ["candidate"]

    confirmed = service.confirm(candidate["id"])

    assert confirmed["status"] == "confirmed"
    assert _candidate_row(connection_factory, candidate["id"])["status"] == "confirmed"
    with connection_factory() as connection:
        assert connection.execute(
            "SELECT project_id FROM agent_threads WHERE id = ?",
            (thread["id"],),
        ).fetchone()[0] == alpha["id"]


def test_reject_preserves_candidate_audit_without_updating_thread(
    tmp_path: Path,
) -> None:
    service, connection_factory, thread, alpha, _beta = _make_services(
        tmp_path,
        classifier=lambda _question: (
            '{"projectId": "' + alpha["id"] + '", "confidence": 0.9}'
        ),
    )
    candidate = service.route("拒绝 Alpha 项目") ["candidate"]

    rejected = service.reject(candidate["id"])

    assert rejected["status"] == "rejected"
    row = _candidate_row(connection_factory, candidate["id"])
    assert row["status"] == "rejected"
    assert row["reviewed_at"] == 2_000
    with connection_factory() as connection:
        assert connection.execute(
            "SELECT project_id FROM agent_threads WHERE id = ?",
            (thread["id"],),
        ).fetchone()[0] is None


def test_confirm_with_other_project_marks_candidate_changed_and_retargets_thread(
    tmp_path: Path,
) -> None:
    service, connection_factory, thread, alpha, beta = _make_services(
        tmp_path,
        classifier=lambda _question: (
            '{"projectId": "' + alpha["id"] + '", "confidence": 0.9}'
        ),
    )
    candidate = service.route("改到 Alpha 项目") ["candidate"]

    changed = service.confirm(candidate["id"], project_id=beta["id"])

    assert changed["status"] == "changed"
    assert changed["projectId"] == beta["id"]
    assert _candidate_row(connection_factory, candidate["id"])["status"] == "changed"
    with connection_factory() as connection:
        assert connection.execute(
            "SELECT project_id FROM agent_threads WHERE id = ?",
            (thread["id"],),
        ).fetchone()[0] == beta["id"]


def test_confirm_rejects_unknown_project_id(tmp_path: Path) -> None:
    service, _connection_factory, _thread, alpha, _beta = _make_services(
        tmp_path,
        classifier=lambda _question: (
            '{"projectId": "' + alpha["id"] + '", "confidence": 0.9}'
        ),
    )
    candidate = service.route("确认 Alpha 项目") ["candidate"]

    with pytest.raises(ValueError, match="project id"):
        service.confirm(candidate["id"], project_id="missing")
