"""research_api-class arXiv tools: arxiv_search / arxiv_get_paper / arxiv_ingest.

arXiv's positioning: the **first publication venue** for cutting-edge AI papers (same-day updated preprints),
complementing OpenAlex (authoritative/citations). Same research_api category.

- arxiv_search: search arXiv by keyword/category/year (Atom feed, free without a key, 3-second rate limit);
- arxiv_get_paper: fetch a single paper's metadata by arXiv id (including PDF link);
- arxiv_ingest: **download+parse+ingest pipeline**—write the paper metadata into the local catalog
  (venue='arXiv', peer-level with CVPR/journals, auto-merged by title to prevent duplicates when a paper
  is first posted to arXiv and later published), optionally download PDF + MinerU parse + embedding ingest,
  after which local full-text retrieval/Q&A can hit it.

Rate limit: arXiv officially requires ≥3 seconds between requests; module-level throttling.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from backend.agent.tools import schemas
from backend.agent.tools.base import ToolDef, ToolResult

API_BASE = "https://export.arxiv.org/api/query"
USER_AGENT = "AIPaperbase Agent-arxiv/0.1 (personal research)"
RATE_LIMIT_SECONDS = 3.0
ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

_last_request = 0.0


def _throttle() -> None:
    """arXiv 3-second rate limit: sleep the remainder if less than 3 seconds since the last request."""
    global _last_request
    now = time.monotonic()
    wait = RATE_LIMIT_SECONDS - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _fetch(params: dict[str, Any]) -> bytes:
    _throttle()
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _parse_feed(payload: bytes) -> dict[str, Any]:
    """Parse an Atom feed: {total, entries: [{arxiv_id, title, authors, abstract, year,
    published, doi, pdf_url, paper_url, category}]}"""
    root = ET.fromstring(payload)
    total = int(root.findtext(f"{OPENSEARCH}totalResults", "0") or 0)
    entries: list[dict[str, Any]] = []
    for elem in root.findall(f"{ATOM}entry"):
        entry_id = (elem.findtext(f"{ATOM}id", "") or "").strip()
        arxiv_id = entry_id.rsplit("/", 1)[-1] if entry_id else ""
        arxiv_id = ARXIV_VERSION.sub("", arxiv_id)
        title = " ".join((elem.findtext(f"{ATOM}title", "") or "").split())
        summary = " ".join((elem.findtext(f"{ATOM}summary", "") or "").split())
        published = (elem.findtext(f"{ATOM}published", "") or "")[:10]
        year = int(published[:4]) if published and published[:4].isdigit() else 0
        authors = [
            " ".join((author.findtext(f"{ATOM}name", "") or "").split())
            for author in elem.findall(f"{ATOM}author")
        ]
        links = {
            (link.get("title") or ""): (link.get("href") or "")
            for link in elem.findall(f"{ATOM}link")
        }
        primary = elem.find(f"{ARXIV_NS}primary_category")
        category = primary.get("term", "") if primary is not None else ""
        entries.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": authors,
            "abstract": summary,
            "published": published,
            "year": year,
            "doi": (elem.findtext(f"{ARXIV_NS}doi", "") or "").strip(),
            "pdf_url": links.get("pdf", "") or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""),
            "paper_url": links.get("alternate", "") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
            "category": category,
        })
    return {"total": total, "entries": entries}


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    """Compact output (saves tokens): core fields + truncated abstract."""
    return {
        "arxiv_id": entry["arxiv_id"],
        "title": entry["title"],
        "authors": entry["authors"][:5],
        "year": entry["year"],
        "category": entry["category"],
        "doi": entry["doi"],
        "pdf_url": entry["pdf_url"],
        "abstract": entry["abstract"][:400],
    }


def _fetch_by_id(arxiv_id: str) -> list[dict[str, Any]]:
    arxiv_id = ARXIV_VERSION.sub("", arxiv_id.strip())
    payload = _fetch({"id_list": arxiv_id, "max_results": 1})
    return _parse_feed(payload)["entries"]


def _arxiv_search(
    ctx: dict, query: str = "", category: str = "", year: int = 0,
    sort: str = "relevance", top_k: int = 5,
) -> ToolResult:
    """arXiv full-text search. query is required; category can limit a top-level category (e.g. cs.CV); year limits the first-publication year."""
    query = (query or "").strip()
    if not query:
        return ToolResult(ok=False, data={"error": "query 必填（arXiv 不支持空检索）"})
    search_query = f'all:"{query}"'
    if category and re.fullmatch(r"[a-z\-]+(\.[A-Z]+)?", category):
        search_query += f' AND cat:{category}'
    if year > 0:
        search_query += f" AND submittedDate:[{year}01010000 TO {year}12312359]"
    top_k = max(1, min(top_k, 20))
    params: dict[str, Any] = {
        "search_query": search_query,
        "start": 0,
        "max_results": top_k,
        "sortBy": "relevance" if sort != "date" else "submittedDate",
        "sortOrder": "descending",
    }
    try:
        feed = _parse_feed(_fetch(params))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, data={"error": f"arXiv 请求失败: {exc}"})
    items = [_compact(e) for e in feed["entries"][:top_k]]
    provenance = [{
        "entity_id": e["arxiv_id"],
        "title": e["title"],
        "section": "arxiv",
        "text": f"arXiv:{e['arxiv_id']}（{e['year']}）{e['title']}",
        "source": "arxiv",
    } for e in feed["entries"][:top_k]]
    return ToolResult(
        ok=True,
        data={"total": feed["total"], "items": items, "note": "arXiv 为预印本首发源，3 秒限速；已是最新内容无需本地同步"},
        provenance=provenance,
        summary=f"arXiv 检索：{feed['total']} 条结果",
    )


def _arxiv_get_paper(ctx: dict, arxiv_id: str = "") -> ToolResult:
    """Fetch full metadata for a single paper by arXiv id."""
    arxiv_id = (arxiv_id or "").strip()
    if not arxiv_id:
        return ToolResult(ok=False, data={"error": "arxiv_id 必填（形如 2401.00001，可带 v1 版本号）"})
    try:
        entries = _fetch_by_id(arxiv_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, data={"error": f"arXiv 请求失败: {exc}"})
    if not entries:
        return ToolResult(ok=False, data={"error": f"arXiv 未找到 {arxiv_id}", "matched": False})
    entry = entries[0]
    out = {
        **entry,
        "authors": entry["authors"],
        "abstract": entry["abstract"],
    }
    provenance = [{
        "entity_id": entry["arxiv_id"],
        "title": entry["title"],
        "section": "arxiv",
        "text": f"arXiv:{entry['arxiv_id']}（{entry['year']}）{entry['title']}",
        "source": "arxiv",
    }]
    return ToolResult(ok=True, data=out, provenance=provenance,
                      summary=f"arXiv {entry['arxiv_id']}：{entry['title'][:60]}")


def _arxiv_ingest(ctx: dict, arxiv_id: str = "", full: bool = True) -> ToolResult:
    """arXiv download+parse+ingest pipeline: ingest into catalog (merge) → download PDF → MinerU parse → vector ingest.

    full=True (default): full pipeline, after which the local catalog/full-text search can hit it;
    full=False: only ingest the metadata into the catalog (merge); PDF handled later.
    """
    arxiv_id = (arxiv_id or "").strip()
    if not arxiv_id:
        return ToolResult(ok=False, data={"error": "arxiv_id 必填（形如 2401.00001）"})
    try:
        entries = _fetch_by_id(arxiv_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, data={"error": f"arXiv 请求失败: {exc}"})
    if not entries:
        return ToolResult(ok=False, data={"error": f"arXiv 未找到 {arxiv_id}", "matched": False})
    entry = entries[0]

    # 1) Ingest into catalog (writable connection; commit-and-close immediately to avoid long-transaction lock)
    from backend.catalog import arxiv_ingest as ingest_mod
    from backend.catalog.database import DEFAULT_DATABASE, connect

    catalog_conn = connect(DEFAULT_DATABASE)
    try:
        try:
            result = ingest_mod.ingest_entry(catalog_conn, entry)
            catalog_conn.commit()
        except Exception as exc:  # noqa: BLE001
            catalog_conn.rollback()
            return ToolResult(ok=False, data={"error": f"arXiv 入库失败: {exc}"})
    finally:
        catalog_conn.close()
    entity_id = result["entity_id"]
    out: dict[str, Any] = {
        "arxiv_id": entry["arxiv_id"],
        "title": entry["title"],
        "entity_id": entity_id,
        "status": result["status"],
        "venue": "arXiv",
        "year": entry["year"],
    }
    if result["status"] == "merged":
        out["note"] = f"已归并到现有实体（先 arXiv 后中稿），匹配到《{result.get('matched_title', '')}》"
    if not full:
        return ToolResult(ok=True, data=out, summary=f"arXiv {arxiv_id} 元数据已入库（status={result['status']}）")

    # 2) Download PDF (planner auto-detects the arxiv source)
    from backend.library import downloader, store as dl_store
    from backend.rag import parse_store, parser, service as rag_service

    ro = connect(DEFAULT_DATABASE, read_only=True)
    stages: dict[str, Any] = {}
    try:
        store_conn = dl_store.connect()
        dl_store.initialize(store_conn)
        try:
            dl = downloader.run_download(ro, store_conn, [entity_id], workers=1)
            stages["download"] = dl
        finally:
            store_conn.close()

        # 3) MinerU parse
        store_ro = dl_store.connect(read_only=True)
        parse_conn = parse_store.connect()
        parse_store.initialize(parse_conn)
        try:
            pr = parser.run_parse(store_ro, parse_conn, [entity_id], on_progress=None)
            stages["parse"] = pr
        finally:
            store_ro.close()
            parse_conn.close()

        # 4) Vector ingest (embedding → Qdrant)
        parse_conn = parse_store.connect(read_only=True)
        try:
            st = rag_service.ingest_parsed(ro, parse_conn, [entity_id])
            stages["ingest"] = st
        finally:
            parse_conn.close()
    except Exception as exc:  # noqa: BLE001
        out["stages"] = stages
        out["error"] = f"下载/解析/入库链路失败（元数据已入库，PDF 可后续手动处理）: {exc}"
        return ToolResult(ok=True, data=out, summary=f"arXiv {arxiv_id} 元数据已入库，全文链路失败")
    finally:
        ro.close()

    out["stages"] = stages
    downloaded = stages.get("download", {}).get("success", 0)
    chunks = stages.get("ingest", {}).get("ingested_chunks", 0)
    return ToolResult(
        ok=True,
        data=out,
        summary=f"arXiv {arxiv_id} 完整闭环：入库({result['status']}) + 下载({downloaded}) + 解析 + 向量入库({chunks} chunks)",
    )


def arxiv_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="arxiv_search",
            description=(
                "在 arXiv（AI 前沿论文首发地，当日更新）检索预印本论文。"
                "适用：① 本地库还没有的最新论文（先于期刊/会议正式发表）② 追某个方向的最新预印本。"
                "query 必填；可用 category 限定一级分类（如 cs.CV、cs.LG、cs.AI），year 限定首发年份。"
                "注意：arXiv 3 秒限速；返回 arXiv id 可用于 arxiv_get_paper / arxiv_ingest。"
            ),
            parameters=schemas.ARXIV_SEARCH,
            handler=_arxiv_search,
            category="research_api",
        ),
        ToolDef(
            name="arxiv_get_paper",
            description=(
                "按 arXiv id（形如 2401.00001）获取单篇预印本完整元数据：标题/作者/摘要/"
                "发布时间/DOI（若有）/PDF 链接。免费调用。用于核验或获取某篇 arXiv 论文详情。"
            ),
            parameters=schemas.ARXIV_GET_PAPER,
            handler=_arxiv_get_paper,
            category="research_api",
        ),
        ToolDef(
            name="arxiv_ingest",
            description=(
                "把 arXiv 论文**下载+解析+入库**到本地库（完整闭环）。"
                "用户说『帮我下载这篇/把 xxx 存进库里』时使用。"
                "流程：元数据入库 catalog（venue='arXiv'，自动按标题归并——先发预印本后投期刊会议"
                "的论文会合并为同一实体不重复）→ 下载 PDF → MinerU 解析 → 向量入库，"
                "之后本地检索/科研问答即可命中。full=false 可只入库元数据。"
            ),
            parameters=schemas.ARXIV_INGEST,
            handler=_arxiv_ingest,
            category="research_api",
        ),
    ]
