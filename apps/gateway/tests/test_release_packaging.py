from __future__ import annotations

import ast
import hashlib
import subprocess
import tomllib
from pathlib import Path


GATEWAY = Path(__file__).resolve().parents[1]
ROOT = GATEWAY.parents[1]


def read(path: Path) -> str:
    assert path.is_file(), f"missing current ChatOS file: {path}"
    return path.read_text(encoding="utf-8")


def test_node_entry_scripts_parse() -> None:
    for name in ("entry-contract.mjs", "verify-entry.mjs", "start_chatos.mjs"):
        path = ROOT / "scripts" / name
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_current_launcher_requires_preflight_and_live_verification() -> None:
    launcher = read(ROOT / "scripts/start_chatos.mjs")
    verifier = read(ROOT / "scripts/verify-entry.mjs")
    batch = read(ROOT / "scripts/start_chatos.bat")
    assert "verify-entry.mjs" in launcher
    assert "'--preflight'" in launcher
    assert "'--live'" in launcher
    assert "No processes were killed" in launcher
    assert "verifyTrackedSources" in verifier
    assert "gatewayIdentity" in verifier
    assert 'node "%~dp0start_chatos.mjs"' in batch


def test_source_launcher_finds_key_preparer_without_old_release_bundle(tmp_path: Path) -> None:
    from wechat_ai.services.wechat_key_preparer_service import _preparer_command

    executable, args = _preparer_command(tmp_path)
    assert executable.is_file()
    assert Path(args[0]) == GATEWAY / "tools/wechat_key_preparer/app.py"
    assert Path(args[0]).is_file()
    assert args[1:] == ["--result-dir", str(tmp_path)]


def test_key_preparer_ui_uses_windows_native_api_without_network_client() -> None:
    source = read(GATEWAY / "tools/wechat_key_preparer/app.py")
    assert "tkinter" not in source
    assert "wm_ctlcolorstatic" in source
    assert "MessageBoxW" in source
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint({"requests", "urllib", "socket", "websocket"})


def test_pinned_key_preparer_source_and_license_are_auditable() -> None:
    base = GATEWAY / "tools/wechat_key_preparer"
    upstream = base / "_upstream/wcdb_key_tool_windows.py"
    provenance = read(base / "UPSTREAM.md")
    normalized = upstream.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == "9de22ccc5070bfc5325ece1bdf4da8ca537eea594b821fc2acd46a62c0e41958"
    assert "79f1b5b92e12c66aa281b4a60a3c478b5f547dfa" in provenance
    assert "MIT" in read(base / "LICENSE.wcdb-key-tool")


def test_current_docs_and_dependencies_define_real_migration_gate() -> None:
    readme = read(ROOT / "README.md")
    agents = read(ROOT / "AGENTS.md")
    contract = read(ROOT / "rules/local-model.md")
    project = tomllib.loads(read(GATEWAY / "pyproject.toml"))
    assert readme.startswith("# ChatOS")
    assert "uv sync --extra migration" in readme
    assert "全量检查、生产索引切换及真实检索验收" in readme
    assert "README.md#验收与提交" in agents
    assert "1024" in contract and "shadow" in contract
    migration = project["project"]["optional-dependencies"]["migration"]
    assert any(item.startswith("lancedb") for item in migration)
    assert any(item.startswith("PyYAML") for item in migration)
