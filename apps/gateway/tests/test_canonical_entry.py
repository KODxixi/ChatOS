from pathlib import Path
import json


def test_gateway_startup_identity_is_fixed_and_content_based(tmp_path: Path) -> None:
    from wechat_ai import runtime_contract

    assert hasattr(runtime_contract, "gateway_source_identity"), "gateway needs code startup identity"
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "example.py"
    source.write_text("version = 1", encoding="utf-8")
    first = runtime_contract.gateway_source_identity(tmp_path)
    assert first == runtime_contract.gateway_source_identity(tmp_path)
    source.write_text("version = 2", encoding="utf-8")
    assert first != runtime_contract.gateway_source_identity(tmp_path)
    assert len(runtime_contract.GATEWAY_STARTUP_IDENTITY) == 64


def test_config_identity_captures_loaded_bytes(tmp_path: Path) -> None:
    from wechat_ai.config import load_settings

    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'rerankEnabled': True}), encoding='utf-8')
    settings = load_settings(project_root=tmp_path, settings_path=config)
    assert hasattr(settings, 'config_identity'), 'loaded settings need a fixed identity'
    before = settings.config_identity
    config.write_text('{}', encoding='utf-8')
    assert settings.config_identity == before
    assert load_settings(project_root=tmp_path, settings_path=config).config_identity != before
