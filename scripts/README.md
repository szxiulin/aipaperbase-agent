# Maintenance scripts

Standalone tasks run by project maintainers; they do not carry product runtime business logic.

- `collect/`: collect, rebuild, and enrich catalog data;
- `validate/`: sample, load, and simulate topic-calibration results;
- `audit/`: audit OpenAlex metadata coverage;
- `build_fts_index.py`: build the catalog FTS5 search index;
- `agent_probe.py`: maintainer probe for agent behavior.

Catalog recollection requires `requirements-catalog.txt`, network access, and source review. Its entry point is `.venv/bin/python scripts/collect/build_catalog.py`. Normal users do not need it: `./run.sh` builds the product database from the committed CSV files without recollecting them.

Abstract-enrichment entry point:

```bash
python scripts/collect/enrich_abstracts.py \
  --hf-parquet /tmp/apexpaperrag-all-papers.parquet \
  --reviewarena-tmlr /tmp/apexpaperrag-reviewarena-tmlr.parquet \
  --official-pages
```

Web-page caches are written only to `/tmp/apexpaperrag-abstract-cache`; the project directory keeps only the unified fields and quality reports.

Topic-classification calibration:

```bash
python scripts/validate/sample_topic_evaluation.py
python scripts/validate/load_topic_evaluation.py
```

The first command generates a reproducible stratified sample using a fixed version number and entity IDs; the second loads the sample and review files into a local database.

---

## 中文

这里存放项目维护者执行的独立任务，不承载产品运行时业务逻辑。

- `collect/`：采集、重建和补全目录数据；
- `validate/`：抽样、载入和模拟主题分类校准结果；
- `audit/`：核验 OpenAlex 元数据覆盖；
- `build_fts_index.py`：构建目录 FTS5 检索索引；
- `agent_probe.py`：维护者使用的 Agent 行为探针。

重新采集目录需要安装 `requirements-catalog.txt`、访问网络并人工检查来源，入口是 `.venv/bin/python scripts/collect/build_catalog.py`。普通用户无需运行它；`./run.sh` 会直接使用仓库已提交的 CSV 构建产品数据库，不会重新采集。

摘要补全入口：

```bash
python scripts/collect/enrich_abstracts.py \
  --hf-parquet /tmp/apexpaperrag-all-papers.parquet \
  --reviewarena-tmlr /tmp/apexpaperrag-reviewarena-tmlr.parquet \
  --official-pages
```

网页缓存只写入 `/tmp/apexpaperrag-abstract-cache`，项目目录只保留统一后的字段和质量报告。

主题分类校准：

```bash
python scripts/validate/sample_topic_evaluation.py
python scripts/validate/load_topic_evaluation.py
```

第一条命令使用固定版本号和实体 ID 生成可复现分层样本；第二条将样本与审阅文件加载到本地数据库。
