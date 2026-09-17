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


def gateway_source_identity(root: Path) -> str:
    """Hash source bytes using the same ordering/framing as the repository gate."""
    files = [p for p in (root / "src").rglob("*") if p.is_file() and p.suffix in {".py", ".sql"}]
    files.extend(root / name for name in ("pyproject.toml", "uv.lock") if (root / name).is_file())
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(file.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


GATEWAY_SOURCE_ROOT = Path(__file__).resolve().parents[2]
# Process-start snapshot, never computed from a replaced file at request time.
GATEWAY_STARTUP_IDENTITY = gateway_source_identity(GATEWAY_SOURCE_ROOT)


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
