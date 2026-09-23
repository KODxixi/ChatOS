# wx_note_images.py — 上游证据与本地实现说明

本工具不做任何逆向破解：它复用的是**微信官方客户端自己落盘的缓存文件**，
原理与路径均由社区公开取证实验验证。本文记录来源、已验证事实与本地偏差。

## 验证过的事实（上游证据）

| 版本 | 收藏/笔记媒体缓存路径 | 形态 |
| --- | --- | --- |
| 微信 4.x | `<数据目录>\xwechat_files\<wxid>\business\favorite\temp\` | 明文，文件名 `微信图片_年月日时分秒_序号.jpg`；同一张图缓存 小缩略图 / 中缩略图 / 原图 三份，**原图为三者中最大**，且与"另存为/下载"文件哈希一致 |
| 微信 3.x | `<数据目录>\WeChat Files\<wxid>\FileStorage\Fav\Temp\<hash>\res\` | 明文，缩略图文件名带 `_th` 尾巴；大图在点开后缓存 |
| 微信 3.x | `FileStorage\Fav\Data\<xx>\<md5>`、`FileStorage\Fav\Thumb\<xx>\<md5>` | 无扩展名，单字节 XOR 加密（与聊天图片 dat 同法），解密后与 Temp 明文哈希一致 |

关键性质（决定本工具行为）：

- 图片**只有在本机打开/浏览过后才会缓存**，未浏览的笔记图片不在本地。
- 收藏缓存**不区分"笔记"还是"图片与视频收藏"**，导出的是全部收藏媒体。
- 收藏图片另存大小 = 大图缓存；从收藏**转发**出去的图片会被再次压缩（另存 > 转发）。

来源（均为公开文章/仓库，未复制其代码，仅引用机制事实）：

1. 【微信取证篇】PC微信4.0版本图片收藏功能缓存位置的变更（蘇小沐，2025-04）
   https://cn-sec.com/archives/3985350.html （Wayback 快照可用）
2. 【微信取证篇】从微信收藏图片看微信存储详细记录（蘇小沐，2024-04）
   https://rivers.chaitin.cn/blog/cq94ng10lnechd243qh0
   CSDN 镜像：https://dfirdd.blog.csdn.net/article/details/138049745
3. WQdream/wx-cache-read —— 微信收藏解析 GUI 工具（PyQt5），同类思路：
   https://github.com/WQdream/wx-cache-read
4. xaoyaoo/PyWxDump —— 微信数据库解密与聊天记录导出（收藏导出不是其功能重点）：
   https://github.com/xaoyaoo/PyWxDump

## 本地实现偏差

- **数据目录发现**：注册表键 `HKCU\Software\Tencent\xwechat`（4.x）与
  `HKCU\Software\Tencent\WeChat`（3.x）的 `FileSavePath`/`InstallPath` 为尽力探测；
  找不到时回退到 `Documents\xwechat_files`、`Documents\WeChat Files`，并支持 `--root` 手动指定。
- **去重**：按文件内容 MD5 全局去重（同图在 Data/Thumb/Temp 的多份副本只导出一份）。
- **选原图**（默认，`--keep-thumbs` 关闭）：4.x 按 `微信图片_<时间戳>` 前缀分组取体积最大者；
  3.x 剔除 `*_th*`。同秒预览多张图时前缀会碰撞，属已知启发式误差，可加 `--keep-thumbs` 全量导出。
- **3.x 加密副本**（`--decrypt-dat`）：用常见图片文件头（JPEG/PNG/GIF/BMP/TIFF/WEBP）异或嗅探
  单字节密钥后整体还原，与社区公开的 dat 解密原理一致；无法识别文件头则跳过并报告。
- **不解析收藏数据库**：3.x `FavData.db` / 4.x `favorite.db` 为 SQLCipher 加密，需要内存密钥，
  超出本工具范围；如需按收藏条目结构（标题、时间、内容顺序）精确导出，建议使用
  PyWxDump（数据库解密）或 wx-cache-read（GUI 收藏解析）。

## 手动方案（零工具，按单篇笔记精确导出）

1. 电脑版微信打开目标笔记。
2. 在笔记编辑区 `Ctrl+A` 全选 → `Ctrl+C` 复制。
3. 粘贴到 Word / WPS 文档（图片会以图片对象进入文档）。
4. 保存文档；如需批量取图：把 `.docx` 复制一份改名为 `.zip`，解压后
   `word\media\` 目录即为该文档内全部图片（按插入顺序命名）。
5. 手机上无批量入口：只能逐张长按"保存到手机"，或整篇笔记"保存为长图"。

## 维护风险

- 微信客户端升级可能调整缓存目录/命名（4.0 相对 3.x 已改过一次）；
  若脚本扫描为空，先用 `--dry-run --verbose` 确认实际路径，再以 `--root` 指定。
- 缓存是"浏览过才存在"，且微信清理缓存后文件会消失——重要笔记建议及时导出。
