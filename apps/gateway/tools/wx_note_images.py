#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wx_note_images.py — 批量下载微信笔记/收藏中的图片与视频（本地缓存提取）

原理（社区取证实验已验证，详见同目录 UPSTREAM.md）：
  * 微信 4.x：在电脑上打开过收藏/笔记后，原图会缓存在
        <数据目录>\\xwechat_files\\<wxid>\\business\\favorite\\temp\\
    文件名形如 "微信图片_年月日时分秒_序号.jpg"。同一张图会缓存 小缩略图 /
    中缩略图 / 原图 三份，原图体积最大，且与"另存为"得到的文件哈希一致（明文、未加密）。
  * 微信 3.x：明文缓存在
        <数据目录>\\WeChat Files\\<wxid>\\FileStorage\\Fav\\Temp\\<hash>\\res\\
    （缩略图文件名带 _th 尾巴）；另有同内容的加密副本（单字节 XOR，无扩展名）：
        FileStorage\\Fav\\Data\\<xx>\\<md5>    FileStorage\\Fav\\Thumb\\<xx>\\<md5>
    本工具可用 --decrypt-dat 按图片文件头嗅探 XOR 密钥并解密还原。

限制（务必阅读）：
  1. 只能导出"本机已打开/浏览过"的笔记与收藏图片——缓存机制决定，未打开过的不在本地。
  2. 缓存不区分"笔记"还是"图片与视频收藏"，导出的是全部收藏媒体（含链接首图等）。
     如需按单篇笔记精确导出：电脑版微信打开笔记 → Ctrl+A 全选 → 复制 →
     粘贴到 Word/WPS → 从文档提取图片（见 UPSTREAM.md 的"手动方案"）。
  3. 收藏数据库（3.x FavData.db / 4.x favorite.db）已加密，本工具不解析；
     如需按收藏条目结构化导出，可参考 wx-cache-read（GUI）或 PyWxDump（聊天记录导出）。

用法：
  python tools/wx_note_images.py --dry-run                 # 先预览将导出哪些文件
  python tools/wx_note_images.py                            # 全量导出到当前目录
  python tools/wx_note_images.py --out D:\\out\\wechat_imgs # 指定输出目录
  python tools/wx_note_images.py --root "D:\\WeChat Files"  # 微信数据目录不在默认位置时
  python tools/wx_note_images.py --decrypt-dat              # 额外解密 3.x Data/Thumb 加密副本
  python tools/wx_note_images.py --keep-thumbs              # 保留缩略图/小图（默认只留最大图=原图）

依赖：仅 Python 3.9+ 标准库，无需安装任何第三方包。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

try:
    import winreg  # Windows only
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 明文缓存的媒体扩展名（4.x temp 与 3.x Temp/<hash>/res）
MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
}

# 4.x 缓存文件名：微信图片_年月日时分秒_序号.jpg（同一张图三份：小/中/原图，共享时间戳前缀）
V4_PIC_RE = re.compile(r"^(微信图片_\d{14})_")

# 3.x 缩略图命名尾巴
V3_THUMB_RE = re.compile(r"_th(?=\.|$)", re.IGNORECASE)

# 常见图片文件头 → 扩展名（用于 XOR 解密嗅探；微信 dat 为单字节 XOR）
HEADERS = [
    (b"\xff\xd8", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
    (b"RIFF", ".webp"),  # 需再校验 WEBP 四字节
]

# 注册表探测：4.x / 3.x 数据目录
REG_PROBES = [
    (r"Software\Tencent\xwechat", "FileSavePath", "v4"),
    (r"Software\Tencent\WeChat", "FileSavePath", "v3"),
    (r"Software\Tencent\WeChat", "InstallPath", "v3-install"),
]

# 默认数据根目录
DEFAULT_ROOTS = [
    Path.home() / "Documents" / "xwechat_files",   # 微信 4.x
    Path.home() / "Documents" / "WeChat Files",   # 微信 3.x
]

WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# 路径发现
# ---------------------------------------------------------------------------

def find_data_roots(explicit: str | None = None) -> list[tuple[Path, str]]:
    """返回 [(数据根目录, 来源说明)]。explicit 优先，随后注册表，最后默认位置。"""
    roots: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add(p: Path, src: str) -> None:
        try:
            p = p.resolve()
        except OSError:
            return
        if p.exists() and p not in seen:
            seen.add(p)
            roots.append((p, src))

    if explicit:
        add(Path(explicit), "参数 --root")
    if winreg is not None:
        for key_name, value_name, kind in REG_PROBES:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                    raw = winreg.QueryValueEx(key, value_name)[0]
            except OSError:
                continue
            if not raw:
                continue
            p = Path(raw)
            if kind == "v3-install":
                p = p.parent / "WeChat Files"
            add(p, f"注册表 {key_name}\\{value_name}")
    for d in DEFAULT_ROOTS:
        add(d, "默认位置")

    if not roots:
        raise FileNotFoundError(
            "未找到微信数据目录。请用 --root 指定（如 --root \"D:\\WeChat Files\"），"
            "或先在本机电脑版微信里打开目标笔记/收藏图片再运行本工具。"
        )
    return roots


def iter_plain_media(root: Path):
    """遍历根目录下全部明文媒体缓存，产出 (种类, 路径)。

    种类: v4-temp（4.x 收藏缓存）、v3-temp（3.x Temp/<hash>/res）。
    """
    parts = root.parts
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
            continue
        rel = p.parts[len(parts):]
        if "business" in rel and "favorite" in rel and "temp" in rel:
            yield "v4-temp", p
        elif "FileStorage" in rel and "Fav" in rel and "Temp" in rel and "res" in rel:
            yield "v3-temp", p


def iter_encrypted(root: Path):
    """遍历 3.x 加密收藏缓存（Data / Thumb 下无扩展名文件），产出 (种类, 路径)。"""
    parts = root.parts
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix:  # 无扩展名
            continue
        rel = p.parts[len(parts):]
        if "FileStorage" in rel and "Fav" in rel:
            if "Data" in rel:
                yield "v3-data", p
            elif "Thumb" in rel:
                yield "v3-thumb", p


# ---------------------------------------------------------------------------
# 解密与去重
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> tuple[str | None, int | None]:
    """用常见图片文件头异或探测单字节密钥。返回 (扩展名, 密钥)，失败为 (None, None)。"""
    if len(data) < 8:
        return None, None
    for header, ext in HEADERS:
        k = data[0] ^ header[0]
        if all(b ^ k == h for b, h in zip(data[: len(header)], header)):
            if ext == ".webp" and data[8:12] != b"WEBP":
                continue
            return ext, k
    return None, None


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def select_files(candidates: list[tuple[str, Path]], keep_thumbs: bool):
    """去重与选择：

    1. 内容级去重：同一 md5 只保留一个（优先明文、优先排序靠前的路径）。
    2. 4.x：同一 "微信图片_<时间戳>" 前缀只保留体积最大的文件（默认=原图）。
    3. 3.x：丢弃 *_th* 缩略图（默认）。

    返回 (选中列表, 统计字典)。选中项为 (种类, 路径, 输出相对文件名)。
    """
    stats = {"candidates": 0, "duplicates": 0, "thumbs_dropped": 0, "kept": 0}

    # 1. md5 去重
    by_md5: dict[str, tuple[str, Path]] = {}
    for kind, p in candidates:
        stats["candidates"] += 1
        try:
            digest = md5_of(p)
        except OSError:
            continue
        if digest in by_md5:
            stats["duplicates"] += 1
            continue
        by_md5[digest] = (kind, p)

    # 2/3. 缩略图过滤（按 4.x 时间戳前缀分组取最大；3.x 去 _th）
    v4_groups: dict[str, list[tuple[str, Path]]] = {}
    picked: list[tuple[str, Path]] = []
    for kind, p in sorted(by_md5.values(), key=lambda kv: kv[1].name):
        name = p.name
        if kind == "v4-temp":
            m = V4_PIC_RE.match(name)
            if m:
                v4_groups.setdefault(m.group(1), []).append((kind, p))
                continue
        elif kind == "v3-temp" and not keep_thumbs and V3_THUMB_RE.search(name):
            stats["thumbs_dropped"] += 1
            continue
        picked.append((kind, p))

    if not keep_thumbs:
        for prefix, group in v4_groups.items():
            if len(group) == 1:
                picked.append(group[0])
                continue
            largest = max(group, key=lambda kv: kv[1].stat().st_size)
            stats["thumbs_dropped"] += len(group) - 1
            picked.append(largest)
    else:
        picked.extend(v for group in v4_groups.values() for v in group)

    stats["kept"] = len(picked)
    return picked, stats


def output_name(name: str, index: int) -> str:
    """生成安全的输出文件名（保留原名；撞名时由调用方加序号）。"""
    stem, ext = Path(name).stem, Path(name).suffix
    clean = WINDOWS_ILLEGAL.sub("_", stem)[:120]
    base = f"{clean}{ext}" if ext else clean
    return f"{index:04d}_{base}"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    roots = find_data_roots(args.root)

    candidates: list[tuple[str, Path]] = []
    for root, src in roots:
        found = list(iter_plain_media(root))
        if args.verbose:
            print(f"[{src}] {root}  明文媒体: {len(found)}")
        candidates.extend(found)
        if args.decrypt_dat:
            enc = list(iter_encrypted(root))
            if args.verbose:
                print(f"[{src}] 加密候选(Data/Thumb): {len(enc)}")
            candidates.extend(enc)

    if not candidates:
        print("未找到任何微信收藏媒体缓存。请先在本机电脑版微信里打开目标笔记/收藏"
              "（图片需要被浏览过才会缓存），或检查 --root 是否正确。", file=sys.stderr)
        return 2

    picked, stats = select_files(candidates, args.keep_thumbs)

    if args.dry_run:
        print(f"共扫描 {stats['candidates']} 个候选文件："
              f"重复 {stats['duplicates']}，缩略图/小图 {stats['thumbs_dropped']}，"
              f"将导出 {stats['kept']} 个。\n")
        for kind, p in picked:
            print(f"  [{kind}] {p}")
        return 0

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    errors = 0
    manifest = []
    index = 0
    for kind, p in sorted(picked, key=lambda kv: kv[1].name):
        index += 1
        folder = "v3-decrypted" if kind in ("v3-data", "v3-thumb") else kind
        target = out / folder / output_name(p.name, index)
        if target.exists():  # 同名且内容不同时追加序号，避免覆盖
            target = out / folder / f"{target.stem}_{index}_{p.stat().st_size}{target.suffix}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind in ("v3-data", "v3-thumb"):
                data = p.read_bytes()
                ext, key = sniff(data)
                if ext is None:
                    print(f"  ! 无法识别加密文件，跳过: {p}", file=sys.stderr)
                    errors += 1
                    continue
                target = target.with_suffix(ext)
                target.write_bytes(bytes(b ^ key for b in data))
            else:
                shutil.copy2(p, target)
            manifest.append(f"{target.relative_to(out)}\t{p}")
        except OSError as e:
            print(f"  ! 复制失败 {p}: {e}", file=sys.stderr)
            errors += 1

    (out / "manifest.txt").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8"
    )
    print(f"已导出 {index - errors}/{index} 个文件到: {out}")
    print(f"（原始路径清单: {out / 'manifest.txt'}）")
    if stats["duplicates"] or stats["thumbs_dropped"]:
        print(f"去重 {stats['duplicates']} 个重复文件，"
              f"过滤 {stats['thumbs_dropped']} 个缩略图/小图"
              f"（如需全部保留请加 --keep-thumbs）")
    return 3 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="批量下载微信笔记/收藏中的图片与视频（本地缓存提取，只读微信数据）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "机制说明：微信 4.x 缓存在 <数据目录>\\xwechat_files\\<wxid>\\business\\favorite\\temp"
            "（明文）；微信 3.x 明文缓存在 FileStorage\\Fav\\Temp\\<hash>\\res，"
            "另有单字节 XOR 加密副本在 Fav\\Data 与 Fav\\Thumb（加 --decrypt-dat 还原）。\n"
            "注意：仅导出本机浏览过的内容；缓存不区分笔记与图片收藏。"
        ),
    )
    parser.add_argument("--root", help="微信数据根目录（含 xwechat_files 或 WeChat Files 的目录）")
    parser.add_argument("--out", default="wx_note_images",
                        help="输出目录（默认: 当前目录下 wx_note_images）")
    parser.add_argument("--keep-thumbs", action="store_true",
                        help="保留缩略图/小图（默认只保留每组最大图=原图）")
    parser.add_argument("--decrypt-dat", action="store_true",
                        help="同时解密 3.x Data/Thumb 下的 XOR 加密副本")
    parser.add_argument("--dry-run", action="store_true", help="只列出将导出的文件，不写盘")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出扫描明细")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    try:
        return run(args)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
