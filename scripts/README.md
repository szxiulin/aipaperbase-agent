# Maintenance scripts

Standalone tasks run by project maintainers; they do not carry product runtime business logic.

- `collect/`: collect and generate data lists from external sources;
- `validate/`: independently verify data, PDFs, and indexes;
- `migrate/`: data-format or version migration;
- `release/`: generate publishable data snapshots and reports.

Existing catalog-build entry point: `python scripts/collect/build_catalog.py`.

Abstract-enrichment entry point:

```bash
python scripts/collect/enrich_abstracts.py \
  --hf-parquet /tmp/aipaperbase-all-papers.parquet \
  --reviewarena-tmlr /tmp/aipaperbase-reviewarena-tmlr.parquet \
  --official-pages
```

Web-page caches are written only to `/tmp/aipaperbase-abstract-cache`; the project directory keeps only the unified fields and quality reports.

Topic-classification calibration:

```bash
python scripts/validate/sample_topic_evaluation.py
python scripts/validate/load_topic_evaluation.py
```

The first command generates a reproducible stratified sample using a fixed version number and entity IDs; the second loads the sample and review files into a local database.

---

## 中文

这里存放项目维护者执行的独立任务，不承载产品运行时业务逻辑。

- `collect/`：从外部来源采集和生成数据清单；
- `validate/`：独立核验数据、PDF 和索引；
- `migrate/`：数据格式或版本迁移；
- `release/`：生成可发布的数据快照和报告。

现有目录构建入口：`python scripts/collect/build_catalog.py`。

摘要补全入口：

```bash
python scripts/collect/enrich_abstracts.py \
  --hf-parquet /tmp/aipaperbase-all-papers.parquet \
  --reviewarena-tmlr /tmp/aipaperbase-reviewarena-tmlr.parquet \
  --official-pages
```

网页缓存只写入 `/tmp/aipaperbase-abstract-cache`，项目目录只保留统一后的字段和质量报告。

主题分类校准：

```bash
python scripts/validate/sample_topic_evaluation.py
python scripts/validate/load_topic_evaluation.py
```

第一条命令使用固定版本号和实体 ID 生成可复现分层样本；第二条将样本与审阅文件加载到本地数据库。
