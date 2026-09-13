# -*- coding: utf-8 -*-
"""wx_note_images 工具的合成夹具测试（不依赖真实微信数据）。"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "wx_note_images.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("wx_note_images", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _run(*args, cwd):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd,
    )


def build_fixture(tmp_path: Path) -> Path:
    """构造一个模拟微信数据目录：

    - 4.x: xwechat_files/<wxid>/business/favorite/temp（明文 微信图片_*_序号.jpg 三份 + mp4 + 非媒体文件）
    - 3.x: WeChat Files/<wxid>/FileStorage/Fav/Temp/<hash>/res（明文 + _th 缩略图）
    - 3.x: Fav/Data/<xx>/<md5> 与 Fav/Thumb/<xx>/<md5>（单字节 XOR 加密，无扩展名）
    """
    v4 = tmp_path / "xwechat_files" / "wxid_demo" / "business" / "favorite" / "temp"
    v4.mkdir(parents=True)
    (v4 / "微信图片_20250102103000_0.jpg").write_bytes(b"\xff\xd8" + b"A" * 100)    # 小图
    (v4 / "微信图片_20250102103000_1.jpg").write_bytes(b"\xff\xd8" + b"B" * 500)    # 中图
    (v4 / "微信图片_20250102103000_2.jpg").write_bytes(b"\xff\xd8" + b"C" * 3000)   # 原图
    (v4 / "微信图片_20250102103000_3.jpg").write_bytes(b"\xff\xd8" + b"C" * 3000)   # 与原图重复
    (v4 / "微信图片_20250102103001_0.jpg").write_bytes(b"\xff\xd8" + b"D" * 800)
    (v4 / "clip_20250102103000_0.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"E" * 2000)
    (v4 / "readme.txt").write_text("not media", encoding="utf-8")

    v3 = tmp_path / "WeChat Files" / "wxid_demo" / "FileStorage" / "Fav" / "Temp" / "abc123" / "res"
    v3.mkdir(parents=True)
    (v3 / "img_a.jpg").write_bytes(b"\xff\xd8" + b"F" * 1200)
    (v3 / "img_a_th.jpg").write_bytes(b"\xff\xd8" + b"G" * 60)   # 缩略图
    (v3 / "vid_b.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"H" * 900)

    raw_jpg = b"\xff\xd8\xff\xe0" + b"I" * 2500
    data = tmp_path / "WeChat Files" / "wxid_demo" / "FileStorage" / "Fav" / "Data" / "9d"
    thumb = tmp_path / "WeChat Files" / "wxid_demo" / "FileStorage" / "Fav" / "Thumb" / "9d"
    data.mkdir(parents=True)
    thumb.mkdir(parents=True)
    enc = bytes(b ^ 0x45 for b in raw_jpg)
    (data / "9d4a2070a50ed3df8b7dbefdaea48cb0").write_bytes(enc)
    (thumb / "9d4a2070a50ed3df8b7dbefdaea48cb0").write_bytes(enc)
    return tmp_path


def test_default_run_keeps_originals_only(tmp_path):
    fx = build_fixture(tmp_path)
    out = tmp_path / "out"
    r = _run("--root", str(fx), "--out", str(out), cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    v4_out = sorted(p.name for p in (out / "v4-temp").glob("*"))
    v3_out = sorted(p.name for p in (out / "v3-temp").glob("*"))
    # 每组 "微信图片_<时间戳>" 只留最大=原图（序号后缀 _2），小/中图与重复内容剔除
    assert any(n.endswith("微信图片_20250102103000_2.jpg") for n in v4_out)
    assert not any("微信图片_20250102103000_0.jpg" in n or "微信图片_20250102103000_1.jpg" in n
                   for n in v4_out)
    assert not any("_3.jpg" in n for n in v4_out)
    assert any(n.endswith("微信图片_20250102103001_0.jpg") for n in v4_out)
    assert any(n.endswith("clip_20250102103000_0.mp4") for n in v4_out)
    assert any(n.endswith("img_a.jpg") for n in v3_out)
    assert not any("_th" in n for n in v3_out)
    assert any(n.endswith("vid_b.mp4") for n in v3_out)
    assert not (out / "v3-decrypted").exists()                 # 默认不解密
    assert (out / "manifest.txt").exists()

    # 原图内容正确
    kept = next(p for p in (out / "v4-temp").glob("*") if p.name.endswith("_2.jpg"))
    assert kept.read_bytes() == b"\xff\xd8" + b"C" * 3000


def test_keep_thumbs_and_decrypt_dat(tmp_path):
    fx = build_fixture(tmp_path)
    out = tmp_path / "out2"
    r = _run("--root", str(fx), "--out", str(out), "--keep-thumbs", "--decrypt-dat",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    v4_names = [p.name for p in (out / "v4-temp").glob("*")]
    # 小/中/原三份都保留（_0/_1/_2），重复 _3 仍去重
    assert sum("微信图片_20250102103000" in n for n in v4_names) == 3

    v3_names = [p.name for p in (out / "v3-temp").glob("*")]
    assert any("_th" in n for n in v3_names)                            # 缩略图保留

    dec = list((out / "v3-decrypted").glob("*.jpg"))
    assert len(dec) == 1, dec
    assert dec[0].read_bytes() == b"\xff\xd8\xff\xe0" + b"I" * 2500     # XOR 解密还原


def test_dry_run_writes_nothing(tmp_path):
    fx = build_fixture(tmp_path)
    out = tmp_path / "out3"
    r = _run("--root", str(fx), "--out", str(out), "--dry-run", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "将导出" in r.stdout
    assert not out.exists()


def test_nothing_found_returns_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run("--root", str(empty), "--out", str(tmp_path / "o"), cwd=tmp_path)
    assert r.returncode == 2


def test_sniff_unit(mod):
    assert mod.sniff(b"\xff\xd8" + b"\x00" * 16) == (".jpg", 0)
    enc = bytes(b ^ 0x45 for b in (b"\xff\xd8" + b"\x00" * 16))
    assert mod.sniff(enc) == (".jpg", 0x45)
    assert mod.sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == (".png", 0)
    assert mod.sniff(b"GIF89a" + b"\x00" * 16) == (".gif", 0)
    assert mod.sniff(b"\x42\x4d" + b"\x00" * 16) == (".bmp", 0)
    assert mod.sniff(b"\x00" * 16) == (None, None)
    assert mod.sniff(b"\xff\xd8") == (None, None)  # 太短，无法可靠嗅探
