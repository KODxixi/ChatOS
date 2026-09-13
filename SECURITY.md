# Security

- `key/`、`.env*`、`*.key`、`*.pem` 一律 gitignore，绝不提交
- 会话数据包含个人隐私，本体在 D 盘，不进 git 仓库
- 跨 OS 数据交换通过 `bridges/` 契约，不得直接暴露内部 API
- 部署密钥和凭据管理遵循 `C:\AI\_canonical\SAFETY.md` 规范
