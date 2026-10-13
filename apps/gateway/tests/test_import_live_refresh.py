from __future__ import annotations

from pathlib import Path

from fixtures.synthetic_sqlite import create_synthetic_sources
from wechat_ai.config import Settings
from wechat_ai.services import import_service


def test_live_source_refresh_runs_before_source_discovery(
    tmp_path: Path,
) -> None:
    fixtures = create_synthetic_sources(tmp_path / "sources")
    settings = Settings(
        project_root=tmp_path,
        source_dir=fixtures.root,
        library_db=tmp_path / "library" / "wechat-ai.sqlite",
        output_dir=tmp_path / "output",
        lm_base_url="http://127.0.0.1:1234/v1",
    )
    calls: list[str] = []

    def refresh_sources() -> None:
        calls.append("refresh")

    def discover_sources(source_dir: Path):
        calls.append("discover")
        return import_service.discover_sources(source_dir)

    result = import_service.ImportService(
        settings,
        source_refresher=refresh_sources,
        discoverer=discover_sources,
    ).run()

    assert result.inserted_messages == 5
    assert calls[:2] == ["refresh", "discover"]
