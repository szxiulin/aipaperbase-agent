from __future__ import annotations

"""Browser retrieval tools v1 (single-shot): browser_fetch_page (fetch body text) + browser_search (DuckDuckGo lite).

Design principles (researched from browser-use / agent-browser / Playwright MCP):
- What's given to the LLM is a structured text snapshot (body/links), not a screenshot or the full DOM;
- Single-shot: each call opens an independent browser session, reads once and closes—no multi-step interaction loop (interactive v2 deferred);
- Cost control: per-task browser call budget (default 3) + body truncation + hard timeout;
- Safety: only public http/https allowed; rejects file:// / localhost / internal IPs (SSRF guard).
"""

import re
import urllib.parse
from typing import Any

from backend.agent.tools.base import ToolDef, ToolResult

BROWSER_TIMEOUT_MS = 15000
NETWORK_IDLE_MS = 5000
MAX_TEXT = 6000
MAX_LINKS = 20
DEFAULT_BUDGET = 3

_LOCAL_HOST_RE = re.compile(
    r"(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|::1|169\.254\.)", re.I
)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _valid_http_url(url: str) -> bool:
    if not url or len(url) > 2048:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or _LOCAL_HOST_RE.search(host):
        return False
    if "." not in host:  # single-label hostname (http://a) may resolve to an internal address
        return False
    try:
        import ipaddress

        ipaddress.ip_address(host)  # bare IP → reject (local/internal)
        return False
    except ValueError:
        pass
    return True


def _spend_budget(ctx: dict) -> str | None:
    budget = ctx.setdefault("web_budget", {"used": 0, "max": DEFAULT_BUDGET})
    if budget["used"] >= budget["max"]:
        return f"浏览器调用次数已达上限（{budget['max']} 次/任务），请基于已获取的结果作答，或换用本地工具"
    budget["used"] += 1
    return None


def _browser_page(url: str) -> dict:
    """Open the page (wait for JS rendering) → extract title/body/links → close the browser. Returns dict."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
            except Exception:
                pass
            title = (page.title() or "").strip()
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({text: (e.innerText||'').trim().slice(0,80), href: e.href}))"
                ".filter(l => l.href && l.text)",
            ) or []
            return {"title": title, "text": text[:MAX_TEXT], "links": links[:MAX_LINKS]}
        finally:
            browser.close()


def _browser_fetch_page(ctx: dict, url: str, extract: str = "main") -> ToolResult:
    if not _valid_http_url(url):
        return ToolResult(
            ok=False,
            data={"error": "URL 非法或指向内网/本地地址（仅允许公网 http/https）"},
            summary="URL 被拒绝",
        )
    budget_error = _spend_budget(ctx)
    if budget_error:
        return ToolResult(ok=False, data={"error": budget_error}, summary="预算耗尽")
    try:
        page = _browser_page(url)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"打开网页失败：{str(exc)[:120]}"}, summary="网页打开失败")
    data: dict[str, Any] = {"title": page["title"], "url": url, "text": page["text"]}
    if extract in ("links", "all"):
        data["links"] = page["links"]
    provenance = [{
        "entity_id": url,
        "title": page["title"] or url,
        "section": "web",
        "text": f"{page['title'] or url}：{(page['text'] or '')[:200]}",
        "source": "web",
        "url": url,
    }]
    return ToolResult(
        ok=True,
        data=data,
        provenance=provenance,
        summary=f"抓取「{page['title'][:30] or url}」（正文 {len(page['text'])} 字）",
    )


def _extract_real_url(href: str) -> str:
    """Resolve the real URL behind a search-engine redirect link (DDG uddg / Bing ck/a's u param; u is base64url)."""
    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if "uddg=" in href:
        return parsed_qs.get("uddg", [""])[0]
    if "/ck/a" in href:
        encoded = parsed_qs.get("u", [""])[0]
        if not encoded:
            return ""
        try:
            import base64

            return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except Exception:
            return encoded
    return href


def _clean_results(links: list[dict], top_k: int) -> list[dict]:
    results: list[dict] = []
    for link in links:
        text = (link.get("text") or "").strip()
        real = _extract_real_url(link.get("href", ""))
        if len(text) < 8 or not real.startswith("http"):
            continue
        if "duckduckgo.com" in real or "bing.com" in real or "microsoft.com" in real:
            continue
        results.append({"title": text[:80], "url": real})
        if len(results) >= top_k:
            break
    return results


def _ddg_search(query: str) -> list[dict]:
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    page = _browser_page(url)
    return _clean_results(page.get("links", []), 10)


def _bing_search(query: str) -> list[dict]:
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&setlang=en&cc=US&mkt=en-US&ensearch=1")
    page = _browser_page(url)
    return _clean_results(page.get("links", []), 10)


def _browser_search(ctx: dict, query: str, top_k: int = 5) -> ToolResult:
    query = (query or "").strip()
    if not query:
        return ToolResult(ok=False, data={"error": "搜索词不能为空"}, summary="缺少查询词")
    budget_error = _spend_budget(ctx)
    if budget_error:
        return ToolResult(ok=False, data={"error": budget_error}, summary="预算耗尽")
    top_k = max(1, min(top_k, 10))
    try:
        results = _ddg_search(query)
        if not results:  # DDG anti-scraping / no results → fall back to Bing
            results = _bing_search(query)
    except Exception as exc:
        return ToolResult(ok=False, data={"error": f"搜索失败：{str(exc)[:120]}"}, summary="搜索失败")
    results = results[:top_k]
    if not results:
        return ToolResult(
            ok=True, data={"items": [], "total": 0},
            summary="搜索无结果（搜索引擎可能反爬，可换关键词或稍后再试）",
        )
    provenance = [
        {"entity_id": r["url"], "title": r["title"] or r["url"], "section": "web",
         "text": f"{r['title']}：{r['url']}", "source": "web", "url": r["url"]}
        for r in results
    ]
    return ToolResult(
        ok=True,
        data={"items": results, "total": len(results)},
        provenance=provenance,
        summary=f"搜索到 {len(results)} 条结果",
    )


def web_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="browser_fetch_page",
            description=(
                "用真实浏览器打开网页并提取正文文本（自动等待 JS 渲染）。"
                "适合：本地库没有、且无 API 可查的页面内容（论文官网、出版社详情页、动态渲染页面）。"
                "返回标题 + 正文（≤6000 字）+ 可选链接列表。每次调用独立会话，读一次即关闭。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的完整 URL（仅公网 http/https）"},
                    "extract": {"type": "string", "enum": ["main", "links", "all"],
                                "description": "main=只要正文；links=只要链接；all=都要（默认 main）"},
                },
                "required": ["url"],
            },
            handler=_browser_fetch_page,
            category="web",
        ),
        ToolDef(
            name="browser_search",
            description=(
                "通用网页搜索（DuckDuckGo lite，无需 API key）。返回标题+URL 列表（≤10 条）。"
                "适合：本地库搜不到的外部信息、最新动态、通用知识检索。"
                "需要正文内容时，拿到 URL 后再调 browser_fetch_page。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
            handler=_browser_search,
            category="web",
        ),
    ]
