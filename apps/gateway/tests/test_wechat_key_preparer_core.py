from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


CORE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "wechat_key_preparer"
    / "core.py"
)


def load_preparer_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gchat_key_preparer_core", CORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEngine:
    def __init__(self) -> None:
        self._print = print
        self._save_results = lambda *_args: None

    @staticmethod
    def auto_detect_db_dir() -> str:
        return r"C:\private\wechat\db_storage"

    def _scan_memory_raw_key(self, db_dir: str, output: str):
        del output
        assert db_dir == r"C:\private\wechat\db_storage"
        self._save_results(
            [
                (
                    r"message\message_0.db",
                    r"C:\private\wechat\db_storage\message\message_0.db",
                    4 * 1024 * 1024,
                    "11" * 16,
                    bytes(4096),
                )
            ],
            {"11" * 16: [r"message\message_0.db"]},
            {"11" * 16: "aa" * 32},
            db_dir,
            "unused.json",
        )
        return {"11" * 16: "aa" * 32}


def test_preparer_core_writes_gchat_shape_without_path_metadata(
    tmp_path: Path,
) -> None:
    prepare_keys = load_preparer_core().prepare_keys

    count = prepare_keys(tmp_path, engine=FakeEngine())

    result = json.loads(tmp_path.joinpath("all_keys.json").read_text("utf-8"))
    status = json.loads(tmp_path.joinpath("status.json").read_text("utf-8"))
    assert count == 1
    assert result == {
        "message/message_0.db": {
            "enc_key": "aa" * 32,
            "salt": "11" * 16,
            "size_mb": 4.0,
        }
    }
    assert "_db_dir" not in result
    assert r"C:\private" not in json.dumps(result)
    assert status == {"state": "ready", "entryCount": 1, "errorCode": None}


def test_default_result_dir_prefers_chatbox_and_falls_back_to_legacy_gchat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    core = load_preparer_core()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert core.default_result_dir() == tmp_path / "Chatbox" / "data" / "key-preparer"
    (tmp_path / "Gchat").mkdir()
    assert core.default_result_dir() == tmp_path / "Gchat" / "data" / "key-preparer"
    (tmp_path / "Chatbox").mkdir()
    assert core.default_result_dir() == tmp_path / "Chatbox" / "data" / "key-preparer"
