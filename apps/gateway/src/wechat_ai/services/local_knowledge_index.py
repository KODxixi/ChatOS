"""Small local-model bridge for the disposable ChatOS knowledge projection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .knowledge_archiver import archive_library

Runner = Callable[..., subprocess.CompletedProcess[str]]


class LocalKnowledgeIndex:
    def __init__(
        self,
        *,
        library_root: Path,
        cli_path: Path,
        python_command: Sequence[str],
        runner: Runner = subprocess.run,
        retrieval_base_url: str = "http://127.0.0.1:9123/v1",
    ) -> None:
        self.library_root = library_root.resolve()
        self.knowledge_root = self.library_root / "knowledge"
        self.cli_path = cli_path.resolve()
        self.python_command = tuple(python_command)
        self.runner = runner
        self.retrieval_base_url = retrieval_base_url
        self._lock = threading.Lock()
        self._state = self._load_state()
        if self._state.get("status") == "running":
            self._state.update(status="interrupted", stage="idle", error="上次索引进程被中断")
            self._persist()

    @classmethod
    def default(cls, library_root: Path, *, retrieval_base_url: str = "http://127.0.0.1:9123/v1") -> "LocalKnowledgeIndex":
        cli_path = Path(os.environ.get("CHATOS_LOCAL_MODEL_CLI", r"C:\AI\skills\local-model\scripts\cli.py"))
        configured_python = os.environ.get("CHATOS_LOCAL_MODEL_PYTHON")
        if configured_python:
            python_command = (configured_python,)
        elif (venv_python := cli_path.parent.parent / ".venv" / "Scripts" / "python.exe").is_file():
            python_command = (str(venv_python),)
        elif shutil.which("py"):
            python_command = (str(shutil.which("py")), "-3.13")
        else:
            python_command = (str(shutil.which("python") or "python"),)
        return cls(library_root=library_root, cli_path=cli_path, python_command=python_command, retrieval_base_url=retrieval_base_url)

    def start(self) -> bool:
        with self._lock:
            if self._state.get("status") == "running":
                return False
            self._state = {
                "status": "running",
                "stage": "archive",
                "stats": {},
                "error": None,
                "updatedAt": int(time.time() * 1000),
            }
            self._persist()
            return True

    def refresh(self, connection_factory, *, asset_root: Path | None) -> None:
        try:
            archive_stats = archive_library(
                connection_factory,
                library_root=self.library_root,
                asset_root=asset_root,
                retrieval_base_url=self.retrieval_base_url,
            )
            self._update(stage="vectorize", stats=archive_stats)
            if not self.cli_path.is_file():
                raise FileNotFoundError(f"local-model CLI 不存在: {self.cli_path}")
            command = [
                *self.python_command,
                str(self.cli_path),
                "--registry", str(self.knowledge_root / "local-model-registry.yaml"),
                "--db", str(self.knowledge_root / "lancedb"),
                "--kb", "chatos",
                "--json",
                "index",
            ]
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "local-model index failed").strip()[:1000])
            raw_index_stats = json.loads(completed.stdout or "{}")
            if raw_index_stats.get("files_failed", 0):
                raise RuntimeError(f"local-model index incomplete: {raw_index_stats['files_failed']} files failed")
            # 只保留 JSON 可比的标量与 dict。bool 必须排除（它是 int 的子类），
            # 但**不能只留数字**：optimize / prune 这类「清理结果」是 dict —— 2026-10-15
            # 正是因为这里把它们滤掉，「旧版本从不回收、目录涨到 21 GB」才一直隐身。
            index_stats = {
                key: value
                for key, value in raw_index_stats.items()
                if not isinstance(value, bool) and isinstance(value, (int, float, dict))
            }
            self._update(status="completed", stage="ready", stats={**archive_stats, **index_stats}, error=None)
        except Exception as exc:
            self._update(status="failed", stage="idle", error=str(exc)[:1000])

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _update(self, **changes: Any) -> None:
        with self._lock:
            self._state = {**self._state, **changes, "updatedAt": int(time.time() * 1000)}
            self._persist()

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self._status_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {"status": "idle", "stage": "idle", "stats": {}, "error": None, "updatedAt": None}

    @property
    def _status_path(self) -> Path:
        return self.knowledge_root / "index-status.json"

    def _persist(self) -> None:
        self.knowledge_root.mkdir(parents=True, exist_ok=True)
        temporary = self._status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._status_path)
