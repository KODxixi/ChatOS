from __future__ import annotations

import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wechat_ai.services.export_service import ExportResult, ExportServiceError


def load_export_api_module():
    try:
        return importlib.import_module("wechat_ai.api.export_data")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "wechat_ai.api.export_data has not been implemented"
        ) from exc


def make_client(export_runner) -> TestClient:
    export_api = load_export_api_module()
    app = FastAPI()
    app.include_router(
        export_api.create_export_router(export_runner=export_runner),
        prefix="/api",
    )
    return TestClient(app)


def test_export_endpoint_passes_body_to_injected_runner_and_returns_no_path() -> None:
    calls: list[tuple[str, str]] = []

    def run_export(conversation_id: str, export_format: str) -> ExportResult:
        calls.append((conversation_id, export_format))
        return ExportResult(
            format="markdown",
            file_name="Project-abc123.md",
            message_count=7,
        )

    response = make_client(run_export).post(
        "/api/export",
        json={
            "conversationId": "project@chatroom",
            "format": "markdown",
        },
    )

    assert response.status_code == 200
    assert calls == [("project@chatroom", "markdown")]
    assert response.json() == {
        "format": "markdown",
        "fileName": "Project-abc123.md",
        "messageCount": 7,
    }
    assert ":\\" not in response.text
    assert "/exports/" not in response.text


def test_client_filename_cannot_choose_export_target() -> None:
    calls: list[tuple[str, str]] = []

    def run_export(conversation_id: str, export_format: str) -> ExportResult:
        calls.append((conversation_id, export_format))
        return ExportResult(
            format="json",
            file_name="service-generated.json",
            message_count=1,
        )

    response = make_client(run_export).post(
        "/api/export",
        json={
            "conversationId": "../../private-conversation",
            "format": "json",
            "fileName": "../../escape.json",
            "path": r"C:\private\escape.json",
        },
    )

    assert response.status_code == 200
    assert calls == [("../../private-conversation", "json")]
    assert response.json()["fileName"] == "service-generated.json"
    assert "escape.json" not in response.text
    assert r"C:\private" not in response.text


def test_export_endpoint_returns_stable_conversation_not_found_error() -> None:
    def fail_export(_conversation_id: str, _export_format: str) -> ExportResult:
        raise ExportServiceError("CONVERSATION_NOT_FOUND")

    response = make_client(fail_export).post(
        "/api/export",
        json={"conversationId": "missing", "format": "markdown"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "CONVERSATION_NOT_FOUND",
            "message": "未找到指定会话。",
            "retryable": False,
            "details": {},
        }
    }


def test_export_endpoint_returns_sanitized_write_failure() -> None:
    secret = (
        r"C:\private\exports\partial.tmp SELECT raw_payload_json "
        "synthetic-secret-body"
    )

    def fail_export(_conversation_id: str, _export_format: str) -> ExportResult:
        try:
            raise OSError(secret)
        except OSError as cause:
            raise ExportServiceError("EXPORT_FAILED") from cause

    response = make_client(fail_export).post(
        "/api/export",
        json={"conversationId": "project", "format": "json"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "EXPORT_FAILED",
            "message": "导出失败，未生成文件。",
            "retryable": False,
            "details": {},
        }
    }
    assert secret not in response.text
    assert "Traceback" not in response.text
