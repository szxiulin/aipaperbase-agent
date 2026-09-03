# Catalog data

This directory stores the "paper list layer" of AIPaperbase Agent. The list is built before PDF downloads and answers:

1. How many papers a conference or journal should have for a given year;
2. What the stable identifier of each paper is;
3. Whether PDF downloads have any missing, extra, or duplicate papers;
4. Whether the list comes from an official source or a third-party source cross-checked against multiple sources.

## Directory layout

```text
data/catalog/
├── conferences/<VENUE>/<venue>_<year>.csv
├── journals/<VENUE>/<venue>_<year>.csv
├── reports/catalog_summary.csv
├── reports/cross_venue_duplicates.csv
└── reports/validation_issues.csv
```

Conferences and journals are stored separately; the same venue is further split by publication year. CSV filenames always use lowercase venue, e.g. `conferences/CVPR/cvpr_2026.csv`.

## CSV fields

| Field | Meaning |
|---|---|
| `paper_id` | Stable ID within the venue; primary key for later download, dedup, and citation |
| `venue` / `year` / `track` | Publication source, year, and track |
| `title` / `authors` | Official title and authors; authors separated by `; ` |
| `abstract` | Abstract provided by a traceable source; left empty when it cannot be reliably obtained |
| `abstract_source_name` / `abstract_source_url` | Where the abstract actually came from; not assumed to equal the catalog source |
| `abstract_source_tier` / `abstract_fetched_at` | Source tier of the abstract and the time it was fetched |
| `doi` / `arxiv_id` | Saved when available; empty does not mean the list is incomplete |
| `paper_url` / `pdf_url` | Official paper page and the official PDF address |
| `source_name` / `source_url` | The source that actually produced this row of data |
| `source_tier` | `official`, `official_plus_secondary`, or `third_party_crosschecked` |
| `verification_status` | `verified_official`, `crosschecked`, `needs_review`, or `rolling` |
| `list_status` | `final`, or `rolling` while still subject to change |
| `fetched_at` | Fetch time, UTC ISO 8601 |

## Completeness rules

- Do not judge completeness by "roughly the right count"; use the official ID set as the source of truth.
- `paper_id` must be unique and non-empty within a single CSV.
- Title, year, venue, and source URL must be non-empty.
- When there is an abstract, the abstract source name, link, tier, and fetch time must all be present; when there is no abstract, those four must all be empty together.
- Abstracts only accept exact matches on stable identifiers or on "venue + year + normalized title"; do not auto-merge using fuzzy titles.
- `official` means the record was generated directly from the official catalog.
- Third-party data must be explicitly marked in `source_tier` and cross-checked against at least one other source or an official domain / stable ID.
- The official virtual venues for the latest years of ICLR/ICML may also list presentation tracks such as J2C, Blog, and Position Paper, so "titles unique to the official page" must not be merged straight into the main paper list; the difference set is saved in `cross_source_audit.csv` and only merged after the tracks are confirmed.
- Lists for years such as 2026 that do not yet have a final proceedings are marked `rolling` and must not be confused with final lists.
- Workshop, Demo, Tutorial, Front Matter, Doctoral Consortium, and Sister Conference Reprint are excluded from the main list by default.
- The same paper may belong to both a conference presentation and a journal publication, e.g. SIGGRAPH/SIGGRAPH Asia and TOG. Such records are kept separately in the venue lists but written into `cross_venue_duplicates.csv`, and downloaded only once by DOI.

Run `python scripts/collect/build_catalog.py` to regenerate the list; afterwards review `reports/catalog_summary.csv` and `reports/validation_issues.csv`.

Use `scripts/collect/enrich_abstracts.py` to layer-fill abstracts from ACL Anthology, Crossref, Hugging Face, OpenAlex, ReviewArena snapshots, and official paper pages; quality results are written to `reports/abstract_enrichment.json`. Third-party additions are always explicitly marked and must not masquerade as verified in the body.

---

## 中文

本目录保存 AIPaperbase Agent 的“论文清单层”。清单先于 PDF 下载建立，用来回答：

1. 某会议或期刊某年应当有多少篇论文；
2. 每篇论文的稳定标识是什么；
3. PDF 下载后是否存在遗漏、多下或重复；
4. 清单来自官方来源还是经过多源核验的第三方来源。

### 目录

```text
data/catalog/
├── conferences/<VENUE>/<venue>_<year>.csv
├── journals/<VENUE>/<venue>_<year>.csv
├── reports/catalog_summary.csv
├── reports/cross_venue_duplicates.csv
└── reports/validation_issues.csv
```

会议与期刊分开存储；同一 venue 再按出版年拆分。CSV 文件名全部使用小写 venue，例如
`conferences/CVPR/cvpr_2026.csv`。

### CSV 字段

| 字段 | 含义 |
|---|---|
| `paper_id` | venue 内稳定 ID，后续下载、去重、引用的主键 |
| `venue` / `year` / `track` | 出版来源、年份和轨道 |
| `title` / `authors` | 正式题目和作者；作者用 `; ` 分隔 |
| `abstract` | 可追溯来源提供的论文摘要；无法可靠获取时保持为空 |
| `abstract_source_name` / `abstract_source_url` | 摘要实际来自哪里，不默认等同于目录来源 |
| `abstract_source_tier` / `abstract_fetched_at` | 摘要来源等级与本次获取时间 |
| `doi` / `arxiv_id` | 可用时保存；为空不代表清单不完整 |
| `paper_url` / `pdf_url` | 官方论文页和正式 PDF 地址 |
| `source_name` / `source_url` | 实际生成该行数据的来源 |
| `source_tier` | `official`、`official_plus_secondary` 或 `third_party_crosschecked` |
| `verification_status` | `verified_official`、`crosschecked`、`needs_review` 或 `rolling` |
| `list_status` | `final` 或尚会变化的 `rolling` |
| `fetched_at` | 抓取时间，UTC ISO 8601 |

### 完整性规则

- 不以“数量差不多”判定完整；以官方 ID 集合为准。
- `paper_id` 在单个 CSV 内必须唯一且非空。
- 标题、年份、venue 和来源地址必须非空。
- 有摘要时，摘要来源名称、链接、等级和获取时间必须全部存在；没有摘要时，这四项必须同时为空。
- 摘要仅接受稳定标识符或“venue＋年份＋规范化标题”的精确匹配，不使用模糊标题自动合并。
- `official` 表示记录直接从官方目录生成。
- 第三方数据必须在 `source_tier` 中显式标记，并至少使用另一来源或官方域名/稳定 ID 核验。
- ICLR/ICML 最新年份的官方虚拟会场可能同时列出 J2C、Blog、Position Paper 等展示轨道，因此不能把“官方页面独有标题”直接并入主论文清单；差集保存在 `cross_source_audit.csv`，待轨道确认后再决定是否收录。
- 2026 等尚未形成最终 proceedings 的清单标记为 `rolling`，不能与最终清单混淆。
- Workshop、Demo、Tutorial、Front Matter、Doctoral Consortium 和 Sister Conference Reprint 默认不进入主清单。
- 同一论文可能同时属于会议展示和期刊出版，例如 SIGGRAPH/SIGGRAPH Asia 与 TOG。此类记录在 venue 清单中分别保留，但写入 `cross_venue_duplicates.csv`，PDF 下载时按 DOI 只下载一次。

运行 `python scripts/collect/build_catalog.py` 可重新生成清单；运行结束后查看
`reports/catalog_summary.csv` 和 `reports/validation_issues.csv`。

使用 `scripts/collect/enrich_abstracts.py` 可以从 ACL Anthology、Crossref、
Hugging Face、OpenAlex、ReviewArena 快照及官方论文页分层回填摘要；质量结果写入
`reports/abstract_enrichment.json`。第三方补充始终显式标记，不能冒充正文核验。
