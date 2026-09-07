# AIPaperbase Agent

> English: [README.md](./README.md)

> 在你搭建并研读的 AI 论文库之上，运行一个可核验的研究 Agent。

> **状态：个人项目，持续开发中。** 为个人使用而建，目录控制台已稳定；RAG/Agent 层已端到端可用、仍在演进。按你自己的节奏使用。

![控制台概览](screenshots/overview.png)

## 这是什么、给谁用

**如果你是经常读论文的 AI 方向研究者**，AIPaperbase Agent 让你在本地拥有一个关于你跟踪领域的**可信、可核验的索引**——而不是一堆 PDF 和零散清单。

它把 **AI 顶会顶刊的元数据**变成真正能用的东西：

- **一眼看到领域全貌。** 约 12.2 万条记录（20 会议 + 10 期刊，2023–2026）的按 venue、按方向地图：哪个方向热、在哪个会、逐年怎么变——每个数字都能下钻到可审计的论文清单。
- **精确找到你要的。** 每篇论文挂在**两个永不打架的轴**上：**主题树**（研究*什么*，如"图像超分"、"RAG 与知识增强"）+ **方法标签**（怎么做，`diffusion`、`agentic`、`llm-based`…）。结果带**命中依据**（哪些词/benchmark 触发），不是黑箱打分。
- **维护自己的库。** 建集合、下载 PDF（内容去重）、MinerU 解析成 Markdown——全部本地可复现。
- **问有依据的问题。** 科研问答**只基于你放入的论文**作答，引用可点回原文 PDF/章节；Agent 工具还能代你查 arXiv / OpenAlex / 网页。
- **把阅读变成研究资料。** 记录阅读进度、证据笔记、集合研究进展和可编辑比较表，刷新后继续使用。

名字里的 *base* 是本地论文目录、集合与全文库；*agent* 是在明确论文范围内检索证据、提出整理草稿的研究助手。集合修改和模型建议都先展示草稿，由用户确认后写入。

**一切本地优先**：数据与代码都在你的机器上。唯一外发的只有你主动开启的 API 调用（PDF 解析、向量化、LLM、可选的网页抓取）。

## 它怎么工作（一张图）

```text
venue 年度 CSV（已入库，data/catalog）
   → catalog.sqlite：校验 + 实体去重（arXiv/预印本 ↔ 正式发表）+ 完整性检查
        → 分类：主题树（primary + ≤2 附加叶）+ 方法标签
              → 控制台：主题地图、venue 墙、检索、下钻到论文
然后，对你自己选的论文：
   集合 → PDF 下载（内容去重）→ MinerU 转 Markdown
        → 切块 → Embedding → Qdrant（向量库）
              → 检索 → 重排 → LLM 带引用回答
```

两个词典刻意分开：**主题树**回答"研究什么"，**方法标签**回答"怎么做"——所以一篇"用 Agent 做的超分论文"算超分论文，而不是 Agent 论文。

## 60 秒上手

1. 在**论文库 → 公共目录**检索论文，勾选后加入集合。
2. 在**我的论文**查看全文处理状态，记录待读、阅读中或已读。
3. 选择几篇论文，开启固定范围对话或建立比较表。
4. 核对助手给出的论文范围、理由和原文依据，再确认集合修改或保存研究资料。
5. 在**研究资料**继续编辑笔记、比较表与集合研究进展。

## 快速开始

前置：**Python 3.12+**。只浏览目录和管理本地资料不需要 API key、Docker 或 Node.js。

```bash
git clone https://github.com/szxiulin/aipaperbase-agent.git
cd aipaperbase-agent
python3 -m venv .venv
./run.sh
```

首次运行会自动从仓库自带的 CSV 构建公共目录，实测约 2–5 分钟，占用约 1 GB；以后启动会直接复用。打开 <http://127.0.0.1:8765> 即可浏览目录、创建集合和记录研究资料。

需要下载解析、全文问答和 Agent 时，再执行：

```bash
.venv/bin/pip install -r requirements-rag.txt
cp .env.example .env
# 按需填写 .env；不要提交它
docker run -d -p 6333:6333 qdrant/qdrant
./run.sh
```

MinerU 只在解析 PDF 时需要；Embedding 用于向量入库与检索；生成模型用于对话；Reranker 可留空。完整配置见 [`.env.example`](./.env.example)。

### 各功能需要什么

| 你想… | 需要 |
|---|---|
| 浏览 / 检索 / 洞察目录、集合与研究资料 | Python 3.12+，运行 `./run.sh` |
| 下载并解析集合 PDF | 联网；解析需要 MinerU token |
| **RAG 带引用问答** | Qdrant + `EMBEDDING_*`/`GENERATOR_*`（`RERANKER_*` 可选）+ 已下载解析的论文 |
| 科研 Agent 对话（arXiv / OpenAlex / 网页） | 生成模型配置；全文工具另需 Embedding 与 Qdrant |

进程级 `GENERATOR_TOKEN_BUDGET` 保护你的 API 花费。

## 配置参考

完整 key 列表见 [`.env.example`](./.env.example)。平台均为 OpenAI 兼容，换供应商只改 `base_url` + `api_key` + `model`。

## 仓库结构

| 路径 | 说明 |
|---|---|
| [`backend/`](./backend/README.md) | 模块化 Python 单体：目录导入/实体归并、分析、集合、文库、RAG 胶水层、控制台 HTTP API |
| [`raglib/`](./raglib/) | 可复用、零业务依赖的 RAG 核心（切块→向量→检索→重排→生成） |
| [`frontend/`](./frontend/README.md) | 本地控制台单页（原生 JS、无构建）——**中/EN 切换，默认中文** |
| [`data/catalog/`](./data/catalog/README.md) | 年度 venue 元数据 CSV —— 可重建 catalog 的入库来源 |
| [`config/`](./config/README.md) | `topics.json`（主题树）、`methods.json`（方法标签）、评估脚手架 |
| [`scripts/`](./scripts/README.md) | 维护者工具：采集、摘要补全、校验器 |
| [`tests/`](./tests/README.md) | `unittest` 测试集 |

## 诚实说明

- **目录范围**：20 个 AI 会议 + 10 个期刊，2023–2026（约 12.2 万条记录、去重后约 12.1 万实体）；2026 为滚动清单。元数据来自公开节目页/API——对外重分发衍生数据前请自行核对各来源条款。
- **分类是本地、基于证据的基线**——不是官方学科分类，也不是逐篇人工审核。每条标签可复现、可下钻到命中依据。
- **`topics.json` 主题词表当前为中文**；EN 模式经前端镜像词表显示英文（best-effort）。词表原生英文化是已知 TODO。
- **本地优先**：生成的库、下载、解析全文与个人数据不入库；`data/catalog/` CSV 是例外、也是重建来源。
- 真实模型、下载、MinerU 和生产 Qdrant 仍受各服务配置、额度与网络环境影响；发布前的自动化与浏览器验收使用隔离 fixture，不代表所有供应商组合都已验证。
