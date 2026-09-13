# ChatOS Gateway

ChatOS 后端网关（FastAPI/uvicorn），从 chatbox 迁移而来。

## 功能

- 微信数据库解密与导入
- 会话/消息存储与检索
- 活跃度过滤（30天零发言不索引）
- 知识投影与图谱构建
- REST API + OpenAI 兼容接口

## 启动

```bash
uv run --no-sync chatbox serve --port 8912
```

## 配置

- `config.json`：微信数据库路径
- `config/settings.json`：应用设置（活跃度过滤、模型端点等）
