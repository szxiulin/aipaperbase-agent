from __future__ import annotations

"""Tool contract surface: JSON Schemas for all tools (the only contract exposed to the LLM).

venue/topic use enum whitelists: invalid values are rejected at the schema layer, which also guards against injection.
Local tool (catalog/fulltext) schemas live here; exec/web parameters are inlined in their own modules.

Note: the venue enum comes from catalog's 20 conferences + 10 journals (the authoritative list in data/catalog);
if the collection scope changes, update it accordingly. The topic enum matches the 14 topics in config/topics.json.
"""

_VENUES = [
    "AAAI", "ACL", "ACM MM", "COLM", "CVPR", "ECCV", "EMNLP", "ICCV", "ICLR", "ICML",
    "IJCAI", "KDD", "MLSys", "NAACL", "NeurIPS", "SIGGRAPH", "SIGGRAPH Asia", "SIGIR", "WWW", "3DV",
    "AIJ", "IJCV", "JMLR", "TMLR", "TOG", "TIP", "TKDE", "TOIS", "TPAMI", "TVCG",
]
_TOPICS = [
    "大模型", "训练与对齐", "推理与规划", "VLM 与多模态", "高效微调与推理", "Agent 系统", "RAG",
    "工具使用与执行", "多 Agent 协作", "评测与 Harness", "图像生成与恢复", "图像生成与编辑",
    "超分辨率", "图像恢复",
]

SEARCH_PAPERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "关键词，匹配标题/作者/摘要/DOI；可留空仅用过滤器"},
        "filters": {
            "type": "object",
            "properties": {
                "venues": {"type": "array", "items": {"type": "string", "enum": _VENUES}, "description": "限定会议/期刊，可多选"},
                "years": {"type": "array", "items": {"type": "integer", "minimum": 2023, "maximum": 2026}},
                "topics": {"type": "array", "items": {"type": "string", "enum": _TOPICS}},
                "venue_type": {"type": "string", "enum": ["conference", "journal"]},
            },
        },
        "sort": {"type": "string", "enum": ["relevance", "year_desc"],
                 "description": "relevance=按相关度（找类似论文用）；year_desc=按年份倒序（列清单用）"},
        "page": {"type": "integer", "minimum": 1, "description": "页码，默认 1"},
        "page_size": {"type": "integer", "minimum": 0, "maximum": 50,
                      "description": "返回条数；0=只要统计不要条目（问'有多少/分布'时用，最省 token）"},
        "aggregate": {"type": "array", "items": {"type": "string", "enum": ["venue", "year", "topic"]},
                      "description": "要统计分布时传，如 ['venue','year']"},
    },
    "required": [],
}

LIST_FACETS = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["venues", "topics", "years", "collections"],
                 "description": "要枚举什么"},
    },
    "required": ["type"],
}

SEARCH_EVIDENCE = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要在本地论文全文里找什么（必填）"},
        "entity_ids": {"type": "array", "items": {"type": "string"},
                       "description": "限定在某几篇论文内检索（可选）"},
        "collection_id": {"type": "string", "description": "限定在某集合内检索（可选）"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "description": "证据预算：进生成上下文的片段数，默认 10"},
    },
    "required": ["query"],
}

GET_PAPER = {
    "type": "object",
    "properties": {
        "entity_id": {"type": "string", "description": "论文实体 ID（如 ape_xxx，search_papers 结果里可拿到）"},
    },
    "required": ["entity_id"],
}

GET_EVIDENCE = {
    "type": "object",
    "properties": {
        "entity_id": {"type": "string", "description": "论文实体 ID（需该论文已解析入库）"},
    },
    "required": ["entity_id"],
}

LIST_COLLECTIONS = {
    "type": "object",
    "properties": {},
    "required": [],
}

# ---- research_api (OpenAlex scholarly search API) ----

OPENALEX_SEARCH = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "检索词（标题/摘要/全文关键词），可留空仅用 filters 精确过滤（省预算）"},
        "filters": {
            "type": "object",
            "properties": {
                "venue": {"type": "string",
                          "description": "期刊/会议名或 ISSN（如 TIP / IEEE Transactions on Image Processing / 1057-7149）"},
                "year": {"type": "integer", "minimum": 1900, "maximum": 2100,
                         "description": "出版年份（按 OpenAlex publication_year 口径，非卷期年）"},
                "type": {"type": "string", "enum": ["article", "proceedings-article", "review", "preprint", "book-chapter"],
                         "description": "文献类型，默认不限定"},
            },
        },
        "sort": {"type": "string", "enum": ["relevance", "cited_by_count", "publication_date"],
                 "description": "relevance=相关度（默认，需 query）；cited_by_count=引用数降序（找高影响力论文）；publication_date=日期倒序"},
        "top_k": {"type": "integer", "minimum": 0, "maximum": 10,
                  "description": "返回条数；0=只要 total 计数不要条目（问'XX 期刊/会议 XX 年有多少篇'时用，最省预算）"},
    },
    "required": [],
}

OPENALEX_GET_WORK = {
    "type": "object",
    "properties": {
        "doi": {"type": "string", "description": "DOI（如 10.1109/tip.2026.3657636，可带 https://doi.org/ 前缀）"},
        "openalex_id": {"type": "string", "description": "OpenAlex Work ID（如 W7126063349，search 结果里可拿到）"},
    },
    "required": [],
}

OPENALEX_GET_AUTHOR = {
    "type": "object",
    "properties": {
        "openalex_id": {"type": "string", "description": "OpenAlex Author ID（如 A5023888391）"},
        "name": {"type": "string", "description": "作者姓名（未提供 ID 时按名字查找，取最匹配的 1 个）"},
        "orcid": {"type": "string", "description": "ORCID（形如 0000-0001-2345-6789，精确匹配优先）"},
    },
    "required": [],
}

ARXIV_SEARCH = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索词（标题/摘要/作者全文匹配），必填"},
        "category": {"type": "string",
                     "description": "arXiv 一级分类限定（如 cs.CV / cs.LG / cs.AI / cs.CL），可选"},
        "year": {"type": "integer", "minimum": 1991, "maximum": 2100,
                 "description": "首发年份限定，可选"},
        "sort": {"type": "string", "enum": ["relevance", "date"],
                 "description": "relevance=相关度（默认）；date=按提交时间倒序（看最新）"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数，默认 5"},
    },
    "required": ["query"],
}

ARXIV_GET_PAPER = {
    "type": "object",
    "properties": {
        "arxiv_id": {"type": "string",
                     "description": "arXiv id（形如 2401.00001 或 2401.00001v2，可带版本号）"},
    },
    "required": ["arxiv_id"],
}

ARXIV_INGEST = {
    "type": "object",
    "properties": {
        "arxiv_id": {"type": "string", "description": "要入库的 arXiv id（形如 2401.00001）"},
        "full": {"type": "boolean",
                 "description": "true=完整闭环（入库+下载 PDF+解析+向量入库，默认）；false=只入库元数据"},
    },
    "required": ["arxiv_id"],
}
