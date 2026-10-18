from __future__ import annotations

"""Web tools backed by DeepSeek's Search & Fetch API (https://api-docs.deepseek.com/guides/search/).

web_search returns ranked results with a model-oriented summary; web_fetch returns a page as Markdown.
Both are read-only. The key comes from backend.common.settings (dedicated key, or the chat key when
the chat model is DeepSeek). Billing: free until 2026-11-01 (Beijing), then $0.75 per 1,000 successful calls.
"""

import time
from typing import Any

import httpx

from backend.agent.tools.base import ToolDef, ToolResult
from backend.common import settings

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
FETCH_CHARS = 8000
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
ERRORS = {401: "联网搜索的 API Key 无效", 402: "DeepSeek 账户余额不足", 403: "该网页被安全策略拒绝抓取",
          404: "该网页没有可用内容"}


def available() -> bool:
    return bool(settings.web_search_key())


def _post(url: str, body: dict) -> dict:
    key = settings.web_search_key()
    if not key:
        raise RuntimeError("未配置联网搜索：需要 DeepSeek API Key（在设置里填写）")
    last = ""
    for attempt in range(3):
        try:
            response = httpx.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        except httpx.HTTPError as exc:
            last = str(exc)
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code not in RETRY_STATUS:
                raise RuntimeError(ERRORS.get(response.status_code, f"联网服务返回 HTTP {response.status_code}"))
            last = f"HTTP {response.status_code}"
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"联网服务暂时不可用：{last}")


def _web_search(ctx: dict, query: str = "", count: int = 8, after: str = "", site: str = "") -> ToolResult:
    query = (query or "").strip()
    if not query:
        return ToolResult(ok=False, data={"error": "query 必填"}, summary="缺少 query")
    full = query
    if site:
        full += f" site:{site.strip()}"
    if after:
        full += f" after:{after.strip()}"
    try:
        payload = _post(SEARCH_URL, {"query": full[:128], "count": max(1, min(int(count or 8), 15)),
                                     "ori_question": (ctx.get("query") or "")[:128] or None})
    except RuntimeError as exc:
        return ToolResult(ok=False, data={"error": str(exc)}, summary=str(exc))
    results = payload.get("results") or []
    items: list[dict[str, Any]] = []
    provenance: list[dict] = []
    for r in results:
        items.append({"title": r.get("title"), "url": r.get("url"), "site": r.get("site_name"),
                      "date": r.get("date"), "summary": (r.get("summary") or r.get("snippet") or "")[:700]})
        provenance.append({"url": r.get("url"), "title": r.get("title"), "section": r.get("site_name") or "web",
                           "text": (r.get("snippet") or r.get("summary") or "")[:200], "source": "web"})
    return ToolResult(ok=True, data={"query": full, "items": items}, provenance=provenance,
                      summary=f"联网搜索：{len(items)} 条结果")


def _web_fetch(ctx: dict, url: str = "") -> ToolResult:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ToolResult(ok=False, data={"error": "url 必须是 http(s) 地址"}, summary="url 不合法")
    try:
        payload = _post(FETCH_URL, {"url": url})
    except RuntimeError as exc:
        return ToolResult(ok=False, data={"error": str(exc)}, summary=str(exc))
    content = payload.get("content") or ""
    data = {"url": url, "title": payload.get("title") or "", "content": content[:FETCH_CHARS]}
    if len(content) > FETCH_CHARS:
        data["content_omitted_chars"] = len(content) - FETCH_CHARS
    provenance = [{"url": url, "title": data["title"] or url, "section": "web", "text": content[:200], "source": "web"}]
    return ToolResult(ok=True, data=data, provenance=provenance, summary=f"读取网页：{(data['title'] or url)[:40]}")


def deepseek_web_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="web_search",
            description=("联网搜索（DeepSeek 搜索引擎）。用于本地论文库和 arXiv 覆盖不到的信息：新闻、项目主页、代码仓库、"
                         "博客、榜单、会议动态等。query 用简洁关键词，支持 site:、\"精确短语\"、-排除词。"
                         "返回标题、链接、日期和摘要；需要全文时再调用 web_fetch。"),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词，最多 128 字"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 15, "description": "返回条数，默认 8"},
                    "after": {"type": "string", "description": "只要这个日期之后的结果，如 2026 或 2026-09-01"},
                    "site": {"type": "string", "description": "限定站点，如 github.com"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_web_search,
            category="web",
        ),
        ToolDef(
            name="web_fetch",
            description="读取一个网页的正文（Markdown）。用于打开 web_search 结果或用户给出的链接。",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "完整的 http(s) 链接"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_web_fetch,
            category="web",
        ),
    ]
