from __future__ import annotations

import json
from pathlib import Path

import pytest


class FakeKeyService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.entries = None

    def import_entries(self, entries):
        self.entries = entries
        if self.fail:
            raise RuntimeError("credential store unavailable")
        return {
            "items": [
                {"databaseId": entry["databaseId"], "verified": True}
                for entry in entries
            ]
        }


def test_preparer_start_removes_stale_secret_before_launch(tmp_path: Path) -> None:
    from wechat_ai.services.wechat_key_preparer_service import (
        WechatKeyPreparerService,
    )

    result_dir = tmp_path / "key-preparer"
    result_dir.mkdir()
    result_dir.joinpath("all_keys.json").write_text("secret", encoding="utf-8")
    result_dir.joinpath("status.json").write_text("stale", encoding="utf-8")
    launches: list[Path] = []

    def launch(path: Path) -> None:
        assert not path.joinpath("all_keys.json").exists()
        launches.append(path)

    service = WechatKeyPreparerService(
        result_dir=result_dir,
        key_service=FakeKeyService(),
        launcher=launch,
    )

    assert service.start() == {
        "state": "starting",
        "entryCount": 0,
        "errorCode": None,
    }
    assert launches == [result_dir]


def test_preparer_import_consumes_and_deletes_one_time_result(tmp_path: Path) -> None:
    from wechat_ai.services.wechat_key_preparer_service import (
        WechatKeyPreparerService,
    )

    result_dir = tmp_path / "key-preparer"
    result_dir.mkdir()
    secret = "aa" * 32
    result_dir.joinpath("all_keys.json").write_text(
        json.dumps(
            {
                "message/message_0.db": {
                    "enc_key": secret,
                    "salt": "11" * 16,
                    "size_mb": 4.0,
                }
            }
        ),
        encoding="utf-8",
    )
    result_dir.joinpath("status.json").write_text(
        json.dumps({"state": "ready", "entryCount": 1, "errorCode": None}),
        encoding="utf-8",
    )
    keys = FakeKeyService()
    service = WechatKeyPreparerService(
        result_dir=result_dir,
        key_service=keys,
        launcher=lambda _path: None,
    )

    assert service.status() == {
        "state": "ready",
        "entryCount": 1,
        "errorCode": None,
    }
    imported = service.import_result()

    assert imported["matched"] is True
    assert imported["matchedCount"] == 1
    assert keys.entries[0]["databaseId"] == "message/message_0.db"
    assert secret not in json.dumps(imported)
    assert not result_dir.joinpath("all_keys.json").exists()
    assert not result_dir.joinpath("status.json").exists()


def test_preparer_deletes_one_time_result_when_import_fails(tmp_path: Path) -> None:
    from wechat_ai.services.wechat_key_preparer_service import (
        WechatKeyPreparerService,
    )

    result_dir = tmp_path / "key-preparer"
    result_dir.mkdir()
    result_dir.joinpath("all_keys.json").write_text(
        json.dumps(
            {
                "message/message_0.db": {
                    "enc_key": "aa" * 32,
                    "salt": "11" * 16,
                }
            }
        ),
        encoding="utf-8",
    )
    result_dir.joinpath("status.json").write_text(
        json.dumps({"state": "ready", "entryCount": 1}),
        encoding="utf-8",
    )
    service = WechatKeyPreparerService(
        result_dir=result_dir,
        key_service=FakeKeyService(fail=True),
        launcher=lambda _path: None,
    )

    with pytest.raises(RuntimeError, match="credential store unavailable"):
        service.import_result()

    assert not result_dir.joinpath("all_keys.json").exists()
    assert not result_dir.joinpath("status.json").exists()


def test_preparer_status_rejects_untrusted_error_text(tmp_path: Path) -> None:
    from wechat_ai.services.wechat_key_preparer_service import (
        WechatKeyPreparerService,
    )

    result_dir = tmp_path / "key-preparer"
    result_dir.mkdir()
    private_path = r"C:\private\wechat\all_keys.json"
    result_dir.joinpath("status.json").write_text(
        json.dumps(
            {"state": "error", "entryCount": 0, "errorCode": private_path}
        ),
        encoding="utf-8",
    )
    service = WechatKeyPreparerService(
        result_dir=result_dir,
        key_service=FakeKeyService(),
        launcher=lambda _path: None,
    )

    status = service.status()

    assert status["errorCode"] == "PREPARER_STATUS_INVALID"
    assert private_path not in json.dumps(status)
