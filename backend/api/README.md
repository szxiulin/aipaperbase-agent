# API

Receives requests, calls the matching business module, and returns unified results, while also handling input validation, error formatting, and permission boundaries.

The API layer holds no core business logic; otherwise the frontend, CLI, and Agent integrations would each reimplement it.

---

## 中文

负责接收请求、调用对应业务模块并返回统一结果，同时处理输入校验、错误格式和权限边界。

API 层不保存核心业务逻辑，否则前端、命令行和 Agent 接入会产生重复实现。
