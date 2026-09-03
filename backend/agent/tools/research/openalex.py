from __future__ import annotations

"""research_api-class tools: OpenAlex scholarly search API (atomic tools).

Positioning: provides high quality + authoritative-source confidence for "look it up online"; journal/conference
count verification is a side capability (meta.count).

Verified as of (2026-09-01, official docs + live probing):
- Journals: 10/10 usable ISSN → exact source id filtering (OpenAlex journals have stable sources). Static mapping fixed here.
- Conferences: OpenAlex splits sources by edition ("2022 CVPR", "2009 CVPR", ...) with no series-level source id;
  `/sources?search=` returns only individual year volumes and `display_name.search` filtering is deprecated/failed (400).
  → conference venue can't be filtered exactly; the tool explicitly warns and recommends query full-text search as a fallback (see _venue_parse's note).
- Cost: `search=` is 10x `filter=` ($0.001 vs $0.0001); a single entity `/works/{id|doi:}` is free;
  Bulk DOI (filter=doi:a|b|c ≤100) is left for offline audit scripts, not in agent tools.

Failure semantics: 429 exponential backoff ×5; 30s timeout; 400 passes through the OpenAlex error; 404 → matched=false;
Budget: ctx["openalex_budget"] 8 calls per task (≤ $0.01/task); over-limit prompts convergence.
"""

import os
import time
import urllib.parse
from typing import Any

import httpx

from backend.agent.tools import schemas
from backend.agent.tools.base import ToolDef, ToolResult

API_BASE = "https://api.openalex.org"
TIMEOUT = 30.0
MAX_RETRIES = 5
DEFAULT_BUDGET = 8
ABSTRACT_CHARS = 300
AUTHOR_TOPICS = 3

# 10 journals: OpenAlex source ids (probed and fixed on 2026-09-01; ISSN exact match, reliable)
JOURNAL_SOURCE_IDS: dict[str, str] = {
    "AIJ": "S196139623",
    "TPAMI": "S199944782",
    "IJCV": "S25538012",
    "TOG": "S185367456",
    "TIP": "S4210173141",
    "TKDE": "S30698027",
    "TOIS": "S4394735545",
    "TVCG": "S84775595",
    "JMLR": "S118988714",
    "TMLR": "S4393919742",
}
JOURNAL_ISNS: dict[str, str] = {  # abbreviation → ISSN (for reverse lookup)
    "AIJ": "0004-3702", "TPAMI": "0162-8828", "IJCV": "0920-5691", "TOG": "0730-0301",
    "TIP": "1057-7149", "TKDE": "1041-4347", "TOIS": "1046-8188", "TVCG": "1077-2626",
    "JMLR": "1532-4435", "TMLR": "2835-8856",
}
# 20 conferences (local catalog scope): OpenAlex has no series-level source; identified here for the hint
CONFERENCE_NAMES = {
    "AAAI", "ACL", "ACM MM", "COLM", "CVPR", "ECCV", "EMNLP", "ICCV", "ICLR", "ICML",
    "IJCAI", "KDD", "MLSys", "NAACL", "NeurIPS", "SIGGRAPH", "SIGGRAPH Asia", "SIGIR", "WWW", "3DV",
}

_WORK_SELECT = "id,title,publication_year,doi,primary_location,cited_by_count,authorships,abstract_inverted_index,open_access"
_AUTHOR_SELECT = "id,display_name,orcid,works_count,cited_by_count,summary_stats,last_known_institutions,topics,counts_by_year,works_api_url"


def _mailto() -> str:
    return os.environ.get("OPENALEX_MAILTO", "metadata@apexpaperrag.local")


def _api_key() -> str:
    return os.environ.get("OPENALEX_API_KEY", "")


def _budget_ctx(ctx: dict) -> dict:
    # Budget read from .env: OPENALEX_BUDGET_PER_TASK (default 8 calls/task, ≤$0.01)
    max_budget = int(os.environ.get("OPENALEX_BUDGET_PER_TASK", DEFAULT_BUDGET))
    budget = ctx.setdefault("openalex_budget", {"used": 0, "max": max_budget})
    return budget


def _spend_budget(ctx: dict) -> str | None:
    budget = _budget_ctx(ctx)
    if budget["used"] >= budget["max"]:
        return f"OpenAlex 调用次数已达上限（{budget['max']} 次/任务），请基于已获取的结果作答，或换用本地 search_papers"
    budget["used"] += 1
    return None


def _get(url: str, params: dict[str, str]) -> dict:
    """GET with 429 exponential backoff; returns the parsed JSON dict. Exceptions are raised and turned into ToolResult by the caller."""
    params = dict(params)
    params.setdefault("mailto", _mailto())
    key = _api_key()
    headers = {"User-Agent": f"apexpaperrag mailto:{_mailto()}"}
    if key:
        headers["api-key"] = key
    attempt = 0
    while True:
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
                attempt += 1
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                return {"_status": 404}
            if resp.status_code >= 400:
                try:
                    message = resp.json().get("error") or resp.text[:200]
                except Exception:
                    message = resp.text[:200]
                return {"_status": resp.status_code, "error": message}
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            if attempt < MAX_RETRIES - 1:
                attempt += 1
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"OpenAlex 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAlex 网络错误: {exc}") from exc


def _reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}; reconstruct it back into plain text."""
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted.items():
        for pos in indexes:
            positions[pos] = word
    return " ".join(positions[i] for i in sorted(positions))


def _author_names(work: dict, limit: int = 3) -> list[str]:
    return [
        a.get("author", {}).get("display_name", "")
        for a in (work.get("authorships") or [])[:limit]
        if a.get("author", {}).get("display_name")
    ]


def _compact_work(work: dict) -> dict:
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    venue = source.get("display_name") or loc.get("raw_source_name") or ""
    return {
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "venue": venue,
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "openalex_id": (work.get("id") or "").rstrip("/").rsplit("/", 1)[-1],
        "cited_by_count": work.get("cited_by_count", 0),
        "authors": _author_names(work),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index"))[:ABSTRACT_CHARS],
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "url": work.get("id") or "",
    }


def _venue_parse(venue: str) -> tuple[list[str], str | None]:
    """Resolve a venue name into OpenAlex filter fragments.

    Returns (filters, note): filters is a list of strings to concatenate directly into the filter param; note is a hint for non-exact matches.
    - Journal abbreviation/ISSN → exact source id filter;
    - Conference → can't filter exactly; note suggests using query full-text search as a fallback;
    - Other → try filtering by ISSN (if it looks like one); otherwise return a note.
    """
    v = (venue or "").strip()
    if not v:
        return [], None
    upper = v.upper()
    # ISSN (looks like 1057-7149, or with a hyphen)
    if len(v) in (8, 9) and v.replace("-", "").isdigit():
        return [f"locations.source.issn:{v}"], None
    # Journal abbreviation or full name → static mapping
    if upper in JOURNAL_SOURCE_IDS:
        return [f"locations.source.id:{JOURNAL_SOURCE_IDS[upper]}"], None
    if upper in JOURNAL_ISNS:
        issn = JOURNAL_ISNS[upper]
        return [f"locations.source.issn:{issn}"], None
    if upper in CONFERENCE_NAMES:
        return [], (f"会议「{v}」在 OpenAlex 中按届拆分（无系列级 source id），无法按会议精确过滤；"
                    f"建议把会议名写进 query（如 \"{v} 2026\"）用全文搜索，或改用年份+主题词检索")
    # Unknown → try guessing by ISSN (a pure digit string may have a non-4-4 format)
    return [], f"未知期刊/会议「{v}」，已忽略 venue 过滤；建议用 ISSN 或改为 query 检索"


def _openalex_search(ctx: dict, query: str = "", filters: dict | None = None,
                     sort: str = "relevance", top_k: int = 5) -> ToolResult:
    filters = filters or {}
    budget_error = _spend_budget(ctx)
    if budget_error:
        return ToolResult(ok=False, data={"error": budget_error}, summary="预算耗尽")
    venue = filters.get("venue") or ""
    year = filters.get("year")
    type_filter = filters.get("type") or ""
    filter_parts: list[str] = []
    note: str | None = None
    if venue:
        venue_filters, note = _venue_parse(venue)
        filter_parts.extend(venue_filters)
    if year:
        filter_parts.append(f"publication_year:{int(year)}")
    if type_filter:
        filter_parts.append(f"type:{type_filter}")

    params: dict[str, str] = {"per-page": "1"}
    has_query = bool(query and query.strip())
    if has_query:
        params["search"] = query.strip()  # full-text search (10x cost; description already guides to prefer filter)
    if filter_parts:
        params["filter"] = ",".join(filter_parts)
    # sort: OpenAlex rejects relevance_score sorting for pure filter (no query) requests (400); only used with query search
    if sort == "cited_by_count":
        params["sort"] = "cited_by_count:desc"
    elif sort == "publication_date":
        params["sort"] = "publication_date:desc"
    elif has_query and sort == "relevance":
        params["sort"] = "relevance_score:desc"
    top_k = max(0, min(top_k, 10))
    try:
        data = _get(f"{API_BASE}/works", params)
    except RuntimeError as exc:
        return ToolResult(ok=False, data={"error": str(exc)}, summary="OpenAlex 调用失败")
    if data.get("_status"):
        return ToolResult(ok=False, data={"error": data.get("error") or f"HTTP {data['_status']}"},
                          summary=f"OpenAlex 返回 {data['_status']}")
    total = data.get("meta", {}).get("count", 0)
    items = data.get("results") or []
    compact = [_compact_work(w) for w in items[:top_k]] if top_k > 0 else []
    out: dict[str, Any] = {"total": total, "items": compact}
    if top_k == 0:
        out["note"] = "top_k=0：仅返回 total（OpenAlex 权威计数）"
    if note:
        out["venue_note"] = note
    provenance = [
        {
            "entity_id": it["openalex_id"],
            "title": it["title"],
            "venue": it["venue"],
            "year": it["year"],
            "text": f"{it['title']}（{it['venue']} {it['year']}）引 {it['cited_by_count']}",
            "section": "openalex",
            "source": "openalex",
            "url": it["url"],
        }
        for it in compact
    ]
    return ToolResult(ok=True, data=out, provenance=provenance,
                      summary=f"OpenAlex 命中 {total} 条" + (f"，返回 {len(compact)} 条" if compact else ""))


def _openalex_get_work(ctx: dict, doi: str = "", openalex_id: str = "") -> ToolResult:
    budget_error = _spend_budget(ctx)
    if budget_error:
        return ToolResult(ok=False, data={"error": budget_error}, summary="预算耗尽")
    key = (doi or "").strip() or (openalex_id or "").strip()
    if not key:
        return ToolResult(ok=False, data={"error": "doi 与 openalex_id 至少提供一个"}, summary="缺少参数")
    if doi and doi.strip():
        clean_doi = doi.strip().replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
        path = "/works/doi:" + urllib.parse.quote(clean_doi)
    else:
        oid = openalex_id.strip()
        path = "/works/" + (oid if oid.startswith("W") else oid)
    try:
        data = _get(f"{API_BASE}{path}", {"select": _WORK_SELECT})
    except RuntimeError as exc:
        return ToolResult(ok=False, data={"error": str(exc)}, summary="OpenAlex 调用失败")
    if data.get("_status") == 404:
        return ToolResult(ok=True, data={"matched": False,
                                         "note": "OpenAlex 未收录该 DOI（可能未索引或 DOI 有误）"},
                          summary="OpenAlex 未收录")
    if data.get("_status"):
        return ToolResult(ok=False, data={"error": data.get("error") or f"HTTP {data['_status']}"},
                          summary=f"OpenAlex 返回 {data['_status']}")
    compact = _compact_work(data)
    compact["matched"] = True
    provenance = [{
        "entity_id": compact["openalex_id"],
        "title": compact["title"],
        "venue": compact["venue"],
        "year": compact["year"],
        "text": f"{compact['title']}（{compact['venue']} {compact['year']}）引 {compact['cited_by_count']}",
        "section": "openalex",
        "source": "openalex",
        "url": compact["url"],
    }]
    return ToolResult(ok=True, data=compact, provenance=provenance,
                      summary=f"OpenAlex 单篇：{str(compact['title'])[:40]}")


def _openalex_get_author(ctx: dict, openalex_id: str = "", name: str = "", orcid: str = "") -> ToolResult:
    budget_error = _spend_budget(ctx)
    if budget_error:
        return ToolResult(ok=False, data={"error": budget_error}, summary="预算耗尽")
    if openalex_id and openalex_id.strip():
        path = "/authors/" + openalex_id.strip()
        params: dict[str, str] = {"select": _AUTHOR_SELECT}
    elif orcid and orcid.strip():
        path = "/authors/orcid:" + orcid.strip()
        params = {"select": _AUTHOR_SELECT}
    elif name and name.strip():
        path = "/authors"
        params = {"search": name.strip(), "per-page": "1", "select": _AUTHOR_SELECT}
    else:
        return ToolResult(ok=False, data={"error": "openalex_id / name / orcid 至少提供一个"}, summary="缺少参数")
    try:
        data = _get(f"{API_BASE}{path}", params)
    except RuntimeError as exc:
        return ToolResult(ok=False, data={"error": str(exc)}, summary="OpenAlex 调用失败")
    if data.get("_status") == 404:
        return ToolResult(ok=True, data={"matched": False, "note": "OpenAlex 未找到该作者"},
                          summary="OpenAlex 未找到作者")
    if data.get("_status"):
        return ToolResult(ok=False, data={"error": data.get("error") or f"HTTP {data['_status']}"},
                          summary=f"OpenAlex 返回 {data['_status']}")
    if name and name.strip():
        results = data.get("results") or []
        if not results:
            return ToolResult(ok=True, data={"matched": False, "note": "按姓名未检索到作者"},
                              summary="未找到作者")
        author = results[0]
    else:
        author = data
    summary_stats = author.get("summary_stats") or {}
    insts = [(i.get("display_name") or "") for i in (author.get("last_known_institutions") or []) if i.get("display_name")]
    topics = [(t.get("display_name") or "") for t in (author.get("topics") or [])[:AUTHOR_TOPICS] if t.get("display_name")]
    counts = author.get("counts_by_year") or []
    out = {
        "matched": True,
        "openalex_id": (author.get("id") or "").rstrip("/").rsplit("/", 1)[-1],
        "name": author.get("display_name"),
        "orcid": author.get("orcid"),
        "works_count": author.get("works_count", 0),
        "cited_by_count": author.get("cited_by_count", 0),
        "h_index": summary_stats.get("h_index"),
        "last_known_institutions": insts[:3],
        "topics": topics,
        "recent_years": [{"year": c.get("year"), "works": c.get("works_count"), "citations": c.get("cited_by_count")}
                         for c in sorted(counts, key=lambda x: x.get("year") or 0)[-3:]],
        "works_api_url": author.get("works_api_url") or "",
    }
    provenance = [{
        "entity_id": out["openalex_id"],
        "title": out["name"] or orcid or name,
        "section": "openalex",
        "text": f"作者 {out['name']}：{out['works_count']} 篇，被引 {out['cited_by_count']}，h-index {out['h_index']}",
        "source": "openalex",
    }]
    return ToolResult(ok=True, data=out, provenance=provenance,
                      summary=f"OpenAlex 作者：{out['name']}（h-index {out['h_index']}）")


def openalex_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="openalex_search",
            description=(
                "在 OpenAlex（3.2 亿+ 论文的权威开放学术库）中检索论文。"
                "适用：本地库（20 会议+10 期刊 2023-2026）查不到、需要外部权威源/引用数评估影响力、"
                "或核对某期刊某年的论文数量。"
                "预算提示：能用 filters（venue/year/type）精确限定就优先用 filters（约 $0.0001/次），"
                "避免大而泛的 query 全文搜索（贵 10 倍）；查某期刊某年有多少篇时用 top_k=0 只看 total。"
                "注意：会议（CVPR/NeurIPS 等）在 OpenAlex 中按届拆分，venue 传会议名无法精确过滤，"
                "返回里会有 venue_note 提示，此时请把会议名写进 query 用全文搜索。"
            ),
            parameters=schemas.OPENALEX_SEARCH,
            handler=_openalex_search,
            category="research_api",
        ),
        ToolDef(
            name="openalex_get_work",
            description=(
                "按 DOI 或 OpenAlex ID 获取单篇论文的权威元数据（标题/年份/venue/作者/引用数/是否开放获取/摘要）。"
                "免费调用。用途：① 核验本地论文的权威信息（与本地 get_paper 结果比对）② 追某篇论文的权威记录。"
                "未收录时返回 matched=false（可能未索引或 DOI 有误）。"
            ),
            parameters=schemas.OPENALEX_GET_WORK,
            handler=_openalex_get_work,
            category="research_api",
        ),
        ToolDef(
            name="openalex_get_author",
            description=(
                "按 OpenAlex Author ID / 姓名 / ORCID 获取作者画像：总发文量、总被引、h-index、"
                "最近机构、主要研究主题、近三年产出，以及该作者作品列表的现成 URL（works_api_url）。"
                "按姓名检索时会消歧到最匹配的 1 位作者（同名作者可能不准，建议有 ID/ORCID 时优先）。"
            ),
            parameters=schemas.OPENALEX_GET_AUTHOR,
            handler=_openalex_get_author,
            category="research_api",
        ),
    ]
