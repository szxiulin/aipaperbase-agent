# Backend

The backend is a modular monolith. All modules share a unified paper entity and a single data version, yet keep clear responsibilities:

- `catalog/`: metadata import, validation, the unified paper entity, and catalog querying;
- `analytics/`: count, distribution, trends, topics, maturity/speed, and citation analysis;
- `library/`: PDF selection, download, validation, storage, and parsing;
- `rag/`: chunking, embedding, indexing, retrieval, reranking, generation, and citation;
- `integrations/`: standard export, reference tooling, and Agent integration;
- `api/`: exposes business capabilities to the frontend and external clients;
- `common/`: a small set of genuinely cross-module shared types and base capabilities.

Business logic should live in the matching module, not in API routes or `common/`.

---

## 中文

后端采用模块化单体结构。各模块共享统一论文实体和数据版本，但保持清晰职责：

- `catalog/`：元数据导入、校验、统一论文实体和目录查询；
- `analytics/`：数量、分布、趋势、主题、成熟速度和引用分析；
- `library/`：PDF 选择、下载、校验、存储和解析；
- `rag/`：切分、Embedding、索引、检索、重排、生成和引用；
- `integrations/`：标准导出、文献工具和 Agent 接入；
- `api/`：向前端和外部客户端暴露业务能力；
- `common/`：真正跨模块的少量公共类型和基础能力。

业务逻辑应放在对应模块，不放在 API 路由或 `common/` 中。
