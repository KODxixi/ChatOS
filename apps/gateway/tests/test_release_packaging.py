from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.is_file(), f"missing release file: {relative_path}"
    return path.read_text(encoding="utf-8")


def test_pyinstaller_onedir_bundles_web_and_migrations() -> None:
    spec = read("packaging/gchat.spec")

    assert "web" in spec and "dist" in spec
    assert "migrations" in spec
    assert "COLLECT(" in spec
    assert "gchat_launcher.py" in spec
    assert "Path(SPECPATH).resolve().parent\n" in spec
    assert "Path(SPECPATH).resolve().parent.parent" not in spec
    compile(spec, "packaging/gchat.spec", "exec")


def test_release_bundles_a_separate_elevated_key_preparer() -> None:
    spec = read("packaging/gchat.spec")
    installer = read("packaging/windows/gchat.iss")

    assert "tools" in spec and "wechat_key_preparer" in spec
    assert 'name="chatbox-key-preparer"' in spec
    assert "uac_admin=True" in spec
    assert "console=False" in spec
    assert "chatbox-key-preparer.exe" in installer


def test_key_preparer_ui_uses_windows_native_api_without_tk_runtime() -> None:
    app = read("tools/wechat_key_preparer/app.py")

    assert "tkinter" not in app
    assert "import os" in app
    assert "wintypes.HCURSOR" not in app
    assert "wm_ctlcolorstatic" in app
    assert "MessageBoxW" in app


def test_pinned_key_preparer_source_and_license_are_auditable() -> None:
    source_path = ROOT / "tools/wechat_key_preparer/_upstream/wcdb_key_tool_windows.py"
    license_path = ROOT / "tools/wechat_key_preparer/LICENSE.wcdb-key-tool"
    provenance = read("tools/wechat_key_preparer/UPSTREAM.md")

    assert source_path.is_file()
    assert license_path.is_file()
    normalized_source = source_path.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized_source).hexdigest() == (
        "9de22ccc5070bfc5325ece1bdf4da8ca537eea594b821fc2acd46a62c0e41958"
    )
    assert "79f1b5b92e12c66aa281b4a60a3c478b5f547dfa" in provenance
    assert "MIT" in license_path.read_text("utf-8")
    forbidden = {"requests", "urllib", "http", "socket", "websocket"}
    for relative in (
        "tools/wechat_key_preparer/core.py",
        "tools/wechat_key_preparer/app.py",
        "tools/wechat_key_preparer/_upstream/wcdb_key_tool_windows.py",
    ):
        tree = ast.parse(read(relative))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imported.isdisjoint(forbidden)


def test_frozen_launcher_separates_user_data_from_bundle_assets(tmp_path: Path) -> None:
    launcher = read("packaging/gchat_launcher.py")

    assert "LOCALAPPDATA" in launcher
    assert '"Chatbox"' in launcher
    assert '"Gchat"' in launcher
    assert "sys._MEIPASS" in launcher
    assert "web_dist" in launcher
    assert "dataclasses.replace" in launcher
    compile(launcher, "packaging/gchat_launcher.py", "exec")

    spec = importlib.util.spec_from_file_location(
        "chatbox_frozen_launcher",
        ROOT / "packaging/gchat_launcher.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._select_user_root(tmp_path) == tmp_path / "Chatbox"
    (tmp_path / "Gchat").mkdir()
    assert module._select_user_root(tmp_path) == tmp_path / "Gchat"
    (tmp_path / "Chatbox").mkdir()
    assert module._select_user_root(tmp_path) == tmp_path / "Chatbox"


def test_inno_setup_is_per_user_and_preserves_user_data() -> None:
    installer = read("packaging/windows/gchat.iss")
    lower = installer.lower()

    assert "privilegesrequired=lowest" in lower
    assert "appname=chatbox" in lower
    assert "{localappdata}\\programs\\chatbox" in lower
    assert "smartScreen".lower() in lower
    assert "unsigned" in lower
    assert "uninstalldelete" not in lower
    assert not re.search(r"delete.*(?:appdata|user data|userdata)", lower)


def test_release_script_builds_unsigned_artifacts_sbom_and_hashes_only() -> None:
    script = read("scripts/build_release.ps1")
    lower = script.lower()

    assert "uv sync --frozen --inexact" in lower
    for required in (
        "npm ci",
        "npm test",
        "npm run typecheck",
        "npm run build",
        "pyinstaller",
        "generate_sbom.ps1",
        "get-filehash",
        "iscc.exe",
        "unsigned",
        "smartscreen",
    ):
        assert required in lower
    assert "git push" not in lower
    assert "gh release" not in lower
    assert "signtool" not in lower
    assert "chatbox-$version-windows-x64-unsigned.zip" in lower
    assert "chatbox-$version.cdx.json" in lower


def test_public_docs_define_chatbox_as_data_pool_and_gchat_integration_boundary() -> None:
    readme = read("README.md")
    privacy = read("PRIVACY.md")
    security = read("SECURITY.md")
    spec = read("SPEC.md")

    assert readme.startswith("# Chatbox\n")
    assert "Chatbox 是本地微信数据池" in readme
    assert "Gchat/OpenClaw 是消费该数据池的" in readme
    assert r"D:\Vault-assets\AI_Projects\.wechat-cli" in readme
    assert "--port 8912 --no-browser" in readme
    assert "Chatbox 1.4" in security
    assert "Gchat/OpenClaw consumes the evidence-only loopback API" in privacy
    assert spec.startswith("# Chatbox — Local WeChat Data Pool SPEC\n")
    assert "Gchat/OpenClaw consumes the Chatbox data pool" in spec
    assert "text-embedding-qwen3-embedding-8b" in spec
    assert "text-reranker-8b" in spec
    assert "streams `citations` and" in spec
    assert "never a generated answer" in spec


def test_sbom_script_emits_cyclonedx_for_python_and_npm() -> None:
    script = read("scripts/generate_sbom.ps1")
    lower = script.lower()

    assert "cyclonedx" in lower
    assert "[string]$pythonpath" in lower
    assert "uv pip list" in lower
    assert "python -m pip list" not in lower
    assert "package-lock.json" in lower
    assert "node $nodescriptpath" in lower
    assert "node -e" not in lower
    assert "$packagelockpath | convertfrom-json" not in lower
    assert "pkg:pypi" in lower
    assert "pkg:npm" in lower
    assert 'sort-object { $_["purl"] } -unique' in lower


def test_workflow_builds_and_attests_without_releasing_or_pushing() -> None:
    workflow = read(".github/workflows/release-build.yml")
    lower = workflow.lower()

    assert "workflow_dispatch:" in lower
    assert "contents: read" in lower
    assert "id-token: write" in lower
    assert "attestations: write" in lower
    assert "pytest" in lower
    assert "npm test" in lower
    assert "npm run typecheck" in lower
    assert "npm run build" in lower
    assert "pyinstaller" in lower
    assert "generate_sbom.ps1" in lower
    assert "get-filehash" in lower
    assert "actions/upload-artifact@" in lower
    assert "actions/attest-build-provenance@" in lower
    assert "git push" not in lower
    assert "gh release" not in lower
    assert "softprops/action-gh-release" not in lower
    assert "chatbox_version" in lower
    assert "dist/pyinstaller/chatbox" in lower
    assert "name: chatbox-${{ env.chatbox_version }}-windows-x64-unsigned" in lower


def test_browser_verifier_covers_locked_viewports_and_routes() -> None:
    script = read("scripts/verify_browser.mjs")

    for value in (
        "1440, height: 1000",
        "390, height: 844",
        '"/"',
        '"/projects"',
        '"/knowledge"',
        "outerScrollY",
        "desktopColumnsAligned",
        "consoleIssues",
        "Network.responseReceived",
        "Page.captureScreenshot",
        "Browser.close",
    ):
        assert value in script


def test_development_launcher_starts_gchat_from_its_own_directory() -> None:
    launcher = read("start_chatbox_dev.bat")
    lower = launcher.lower()

    assert 'cd /d "%~dp0"' in lower
    assert "where uv" in lower
    assert "uv run --no-sync chatbox serve --port 8912" in lower
    assert "if errorlevel 1 pause" in lower
