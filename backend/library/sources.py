"""Safe source resolution for the confirmation plan.

The resolver is deliberately dependency-injected: unit tests provide canned
arXiv/OpenAlex candidates, while the HTTP handler may opt into read-only
lookups.  It never downloads a PDF.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from urllib.parse import urlparse

from backend.library.identity import candidate_matches, arxiv_base


OPEN_ACCESS_HOSTS = {
    "arxiv.org", "openaccess.thecvf.com", "proceedings.neurips.cc", "aclanthology.org", "jmlr.org",
    "proceedings.mlr.press", "mlr.press", "proceedings.mlsys.org", "ojs.aaai.org", "ijcai.org",
    "www.ijcai.org", "ecva.net", "www.ecva.net", "openreview.net",
}
RESTRICTED_HOSTS = {"link.springer.com", "springer.com", "ieeexplore.ieee.org", "dl.acm.org", "sciencedirect.com"}


def _classify_url(url: str) -> str:
    host = (urlparse(url).netloc or "").casefold()
    if host in OPEN_ACCESS_HOSTS:
        return "open"
    if host in RESTRICTED_HOSTS:
        return "restricted"
    return "unspecified" if host else "none"


def arxiv_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_base(arxiv_id)}"


class LookupCandidates(list[dict[str, Any]]):
    """Candidates plus compact, user-safe diagnostics for a remote lookup."""

    def __init__(self, values: list[dict[str, Any]] | None = None, *, attempts: list[dict[str, Any]] | None = None):
        super().__init__(values or [])
        self.attempts = attempts or []


CandidateLookup = Callable[[dict[str, Any]], list[dict[str, Any]]]
READ_TIMEOUT_SECONDS = 8
POSITIVE_CACHE_SECONDS = 6 * 60 * 60
NEGATIVE_CACHE_SECONDS = 10 * 60
_CACHE_LOCK = threading.Lock()
_CANDIDATE_CACHE: dict[str, tuple[float, LookupCandidates]] = {}


def _cache_key(kind: str, item: dict[str, Any]) -> str:
    return "|".join((kind, arxiv_base(item.get("arxiv_id", "")), item.get("doi", "").casefold(), item.get("title", "").casefold()))


def _cached(kind: str, item: dict[str, Any], loader: Callable[[], LookupCandidates]) -> LookupCandidates:
    key = _cache_key(kind, item)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CANDIDATE_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
    result = loader()
    # A transport failure says nothing about whether a paper has an OA PDF.
    # Do not turn it into a ten-minute negative cache entry.
    if not any(attempt.get("status") == "error" for attempt in result.attempts):
        with _CACHE_LOCK:
            _CANDIDATE_CACHE[key] = (now + (POSITIVE_CACHE_SECONDS if result else NEGATIVE_CACHE_SECONDS), result)
    return result


def _lookup_error(source: str, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return {"source": source, "status": "error", "reason_code": "source_rate_limited",
                "message": f"{source} 查询被限流，稍后可重试。"}
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc).casefold():
        return {"source": source, "status": "error", "reason_code": "source_timeout",
                "message": f"{source} 查询超时，稍后可重试。"}
    if isinstance(exc, urllib.error.HTTPError):
        return {"source": source, "status": "error", "reason_code": "source_http_error",
                "message": f"{source} 查询服务暂时不可用。"}
    return {"source": source, "status": "error", "reason_code": "source_lookup_failed",
            "message": f"{source} 查询失败，稍后可重试。"}


def _attempts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(getattr(candidates, "attempts", []))


def _json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "AIPaperbase Agent/1.0 (metadata lookup)"})
    with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:  # nosec B310: fixed public APIs
        return json.loads(response.read().decode("utf-8"))


def remote_arxiv_candidates(item: dict[str, Any]) -> LookupCandidates:
    """Read-only arXiv lookup with a bounded request and visible failure state."""
    def load() -> LookupCandidates:
        try:
            from backend.agent.tools.research import arxiv
            if item.get("arxiv_id"):
                query = {"id_list": arxiv_base(item["arxiv_id"]), "max_results": "1"}
            else:
                query = {"search_query": f'all:"{item.get("title", "")}"', "start": "0", "max_results": "5"}
            url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(query)
            request = urllib.request.Request(url, headers={"User-Agent": "AIPaperbase Agent/1.0 (metadata lookup)"})
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:  # nosec B310: fixed public API
                entries = arxiv._parse_feed(response.read().decode("utf-8"))["entries"]
                return LookupCandidates(entries, attempts=[{"source": "arxiv", "status": "ok",
                                                           "candidate_count": len(entries)}])
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return LookupCandidates(attempts=[_lookup_error("arXiv", exc)])
    return _cached("arxiv", item, load)


def remote_openalex_candidates(item: dict[str, Any]) -> LookupCandidates:
    """Read-only OpenAlex lookup. A landing page without an OA PDF is discarded."""
    def load() -> LookupCandidates:
        try:
            if item.get("doi"):
                data = _json("https://api.openalex.org/works/" + urllib.parse.quote("https://doi.org/" + item["doi"], safe=""))
                works = [data]
            else:
                data = _json("https://api.openalex.org/works?" + urllib.parse.urlencode({"search": item.get("title", ""), "per-page": 5}))
                works = data.get("results", [])
            results = []
            for work in works:
                location = work.get("best_oa_location") or work.get("primary_location") or {}
                results.append({
                    "title": work.get("title", ""), "doi": (work.get("doi") or "").removeprefix("https://doi.org/"),
                    "year": work.get("publication_year"),
                    "authors": [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])],
                    "pdf_url": location.get("pdf_url") or "",
                })
            return LookupCandidates(results, attempts=[{
                "source": "openalex", "status": "ok", "candidate_count": len(results),
                "direct_pdf_count": sum(bool(result["pdf_url"]) for result in results),
            }])
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return LookupCandidates(attempts=[_lookup_error("OpenAlex", exc)])
    return _cached("openalex", item, load)


def resolve(item: dict[str, Any], *, arxiv_lookup: CandidateLookup | None = None,
            openalex_lookup: CandidateLookup | None = None) -> dict[str, Any]:
    """Resolve trusted candidates in catalog -> arXiv -> OpenAlex order.

    The first candidate is selected; remaining candidates are retained so a
    failed catalog/arXiv download can continue without another plan request.
    """
    candidates: list[dict[str, Any]] = []
    catalog_url = item.get("catalog_pdf_url") or item.get("download_url") or ""
    source_attempts: list[dict[str, Any]] = []
    if catalog_url and _classify_url(catalog_url) == "open":
        return {"source": "catalog", "source_url": catalog_url, "match_basis": "catalog_pdf_url",
                "downloadable": True, "reason_code": "", "fallback_sources": [], "source_attempts": source_attempts}
    arxiv_id = item.get("arxiv_id") or ""
    if arxiv_id:
        return {"source": "arxiv", "source_url": arxiv_pdf_url(arxiv_id), "match_basis": "arxiv_id",
                "downloadable": True, "reason_code": "", "fallback_sources": [], "source_attempts": source_attempts}
    elif arxiv_lookup:
        arxiv_candidates = arxiv_lookup(item)
        source_attempts.extend(_attempts(arxiv_candidates))
        matched = candidate_matches(item, arxiv_candidates)
        if len(matched) == 1 and matched[0].get("pdf_url"):
            return {"source": "arxiv", "source_url": matched[0]["pdf_url"], "match_basis": matched[0]["match_basis"],
                    "downloadable": True, "reason_code": "", "fallback_sources": [], "source_attempts": source_attempts}
        if len(matched) > 1:
            return {"source": "none", "source_url": "", "match_basis": "", "downloadable": False,
                    "reason_code": "ambiguous_match", "source_attempts": source_attempts}
    if openalex_lookup:
        openalex_candidates = openalex_lookup(item)
        source_attempts.extend(_attempts(openalex_candidates))
        matched = candidate_matches(item, openalex_candidates)
        pdfs = [x for x in matched if x.get("pdf_url") and _classify_url(x["pdf_url"]) != "restricted"]
        if len(pdfs) == 1:
            return {"source": "openalex", "source_url": pdfs[0]["pdf_url"], "match_basis": pdfs[0]["match_basis"],
                    "downloadable": True, "reason_code": "", "fallback_sources": [], "source_attempts": source_attempts}
        if len(pdfs) > 1:
            return {"source": "none", "source_url": "", "match_basis": "", "downloadable": False,
                    "reason_code": "ambiguous_match", "source_attempts": source_attempts}
    failures = [attempt for attempt in source_attempts if attempt.get("status") == "error"]
    if failures:
        # Retain every source attempt for the persisted task; the item headline
        # uses the final failed source because it was the last fallback tried.
        last = failures[-1]
        return {"source": "none", "source_url": "", "match_basis": "", "downloadable": False,
                "reason_code": last["reason_code"], "message": last["message"],
                "fallback_sources": [], "source_attempts": source_attempts}
    return {"source": "none", "source_url": "", "match_basis": "", "downloadable": False,
            "reason_code": "no_download_source", "fallback_sources": [], "source_attempts": source_attempts}
