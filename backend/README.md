# Backend

The backend is a modular monolith. All modules share a unified paper entity and a single data version, yet keep clear responsibilities:

- `catalog/`: metadata import, validation, the unified paper entity, and catalog querying;
- `analytics/`: count, distribution, trends, topics, maturity/speed, and citation analysis;
- `collections/`: user collections, membership drafts, confirmation, and export;
- `library/`: PDF selection, download, validation, storage, and parsing;
- `rag/`: chunking, embedding, indexing, retrieval, reranking, generation, and citation;
- `agent/`: scoped research-agent orchestration and tool dispatch;
- `chats/`: local conversations, messages, snapshots, and ingest-task history;
- `research/`: reading progress, notes, collection progress, and comparison tables;
- `integrations/`: reserved boundary for future external integrations;
- `api/`: exposes business capabilities to the frontend and external clients;
- `common/`: a small set of genuinely cross-module shared types and base capabilities.

Business logic should live in the matching module, not in API routes or `common/`.

---

## 中文

后端采用模块化单体结构。各模块共享统一论文实体和数据版本，但保持清晰职责：

- `catalog/`：元数据导入、校验、统一论文实体和目录查询；
- `analytics/`：数量、分布、趋势、主题、成熟速度和引用分析；
- `collections/`：用户集合、成员草稿、确认写入和导出；
- `library/`：PDF 选择、下载、校验、存储和解析；
- `rag/`：切分、Embedding、索引、检索、重排、生成和引用；
- `agent/`：限定范围的科研 Agent 编排与工具分发；
- `chats/`：本地对话、消息、历史快照和入库任务记录；
- `research/`：阅读进度、笔记、集合进展和比较表；
- `integrations/`：为后续外部集成保留的边界；
- `api/`：向前端和外部客户端暴露业务能力；
- `common/`：真正跨模块的少量公共类型和基础能力。

业务逻辑应放在对应模块，不放在 API 路由或 `common/` 中。
