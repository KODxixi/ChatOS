from __future__ import annotations

from pathlib import Path


def get_project_root() -> Path:
    """Return the repository root for the source checkout."""

    return Path(__file__).resolve().parents[2]


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    """Resolve a configured path without requiring it to exist."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()
