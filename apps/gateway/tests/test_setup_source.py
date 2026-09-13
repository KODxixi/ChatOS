from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_wechat_source_configuration_is_local_and_response_is_redacted(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.setup_service import SetupValidationError, SetupService

    source = tmp_path / "wechat-db"
    database = source / "message" / "message_0.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"encrypted")
    service = SetupService(project_root=tmp_path)

    response = service.configure_wechat_source(str(source))

    assert response == {"detected": True, "restartRequired": False}
    assert str(source) not in json.dumps(response)
    assert json.loads((tmp_path / "config.json").read_text("utf-8")) == {
        "db_dir": str(source.resolve())
    }
    with pytest.raises(SetupValidationError):
        service.configure_wechat_source(str(tmp_path / "missing"))
