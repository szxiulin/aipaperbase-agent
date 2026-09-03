# Local data assets

This directory holds only data and its state, not business code.

| Directory | Contents | Rebuildable |
|---|---|---:|
| `catalog/` | Original yearly lists, source notes, and verification reports | Recollectable, but important evidence |
| `database/` | Unified metadata database actually queried by the product | Generated from catalog and update packs |
| `papers/` | PDFs downloaded or imported on demand by users | Not guaranteed to be fetchable again |
| `parsed/` | Structured content after PDF parsing | Rebuildable from PDFs |
| `indexes/` | Full-text and vector indexes | Rebuildable from parsed results |
| `analyses/` | Versioned analysis results such as topics and trends | Rebuildable from specified data and config |
| `user/` | Paper collections, notes, and personal settings | Not auto-rebuildable; needs protection |

Development code must not write absolute local paths into public metadata.

---

## 中文

本目录只保存数据及其状态，不存放业务代码。

| 目录 | 内容 | 是否可重建 |
|---|---|---:|
| `catalog/` | 原始年度清单、来源说明和核验报告 | 可重新采集，但属于重要证据 |
| `database/` | 产品实际查询的统一元数据库 | 可由 catalog 和更新包生成 |
| `papers/` | 用户按需下载或导入的 PDF | 不保证能够再次获取 |
| `parsed/` | PDF 解析后的结构化内容 | 可由 PDF 重建 |
| `indexes/` | 全文和向量索引 | 可由解析结果重建 |
| `analyses/` | 主题、趋势等版本化分析结果 | 可由指定数据和配置重建 |
| `user/` | 论文集合、笔记和个人设置 | 不可自动重建，需要重点保护 |

开发代码不得把绝对本地路径写入公共元数据。
