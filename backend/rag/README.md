# RAG

Transforms downloaded papers into searchable full-text corpora. This directory is the glue layer between AIPaperbase Agent and the reusable `raglib/`.

- `loader.py`: MinerU Open API client (PDF → Markdown);
- `parse_store.py`: parse manifest `data/parsed/parsed.sqlite` and `data/parsed/{entity_id}.md`;
- `parser.py`: parse orchestration (skip already-parsed / not-downloaded, retry on failure).

- `service.py`: full-text corpus & Qdrant ingestion (chunk → embed → index);
- `evidence.py`: chunk metadata → citation rendering.

Lower-level operations — chunking, embedding, vector indexing, retrieval, and generation — live in the reusable `raglib/` package that this layer calls into.

---

## 中文

把已下载的论文变成可查询的全文语料。本目录是 AIPaperbase Agent 与可复用 `raglib/` 之间的胶水层。

- `loader.py`：MinerU Open API 客户端（PDF → Markdown）；
- `parse_store.py`：解析清单 `data/parsed/parsed.sqlite` 与 `data/parsed/{entity_id}.md`；
- `parser.py`：解析编排（跳过已解析/未下载，失败重试）。

- `service.py`：全文语料与 Qdrant 入库（切块 → Embedding → 索引）；
- `evidence.py`：chunk 元数据 → 引用渲染。

更底层的切块、Embedding、向量索引、检索与生成都在可复用的 `raglib/` 包里，本层直接调用它。
