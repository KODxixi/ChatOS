from __future__ import annotations

import hashlib
import re
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def _application_version() -> str:
    checkout_project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if checkout_project.is_file():
        try:
            project = tomllib.loads(checkout_project.read_text("utf-8"))["project"]
            if project.get("name") == "chatbox" and isinstance(project.get("version"), str):
                return project["version"]
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        raw = version("chatbox")
    except PackageNotFoundError:
        try:
            raw = version("gchat")
        except PackageNotFoundError:
            return "0.0.0-local"
    return re.sub(r"(?i)(\d+\.\d+\.\d+)b(\d+)$", r"\1-beta.\2", raw)


APP_VERSION = _application_version()
API_CONTRACT_VERSION = "2026-08-05"


def web_build_id(web_dist: Path) -> str:
    """Return a deterministic content hash without exposing local paths."""

    digest = hashlib.sha256()
    if web_dist.is_dir():
        files = (candidate for candidate in web_dist.rglob("*") if candidate.is_file())
        for path in sorted(
            files,
            key=lambda candidate: candidate.relative_to(web_dist).as_posix(),
        ):
            relative_path = path.relative_to(web_dist).as_posix().encode("utf-8")
            digest.update(relative_path)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def is_compatible_runtime_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    required_schema = payload.get("requiredSchemaVersion")
    schema = payload.get("schemaVersion")
    return (
        payload.get("apiContractVersion") == API_CONTRACT_VERSION
        and isinstance(schema, int)
        and isinstance(required_schema, int)
        and schema >= required_schema
    )
