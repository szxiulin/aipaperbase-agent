"""ECCV 2026 catalog collection.

Data sources (all verified reachable from this sandbox):
  1. papers.html  noscript block   -> 2864 posters (id + title)
  2. /events/oral  page            -> 28 orals    (id + title)
  3. static JSON orals-posters.json -> first 200 records incl. authors
  4. detail pages poster|oral/{id} -> authors via JSON-LD (preferred) or
     .event-organizers (fallback, '\u22c5'-separated)

The legacy www.ecva.net/papers.php page is NOT updated for 2026, so this
replaces scrape_eccv for year >= 2026.
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "catalog" / "conferences" / "ECCV" / "eccv_2026.csv"

VIRTUAL = "https://eccv.ecva.net/virtual/2026"
PAPERS_PAGE = f"{VIRTUAL}/papers.html"
ORALS_PAGE = f"{VIRTUAL}/events/oral"
STATIC_JSON = "https://eccv.ecva.net/static/virtual/data/eccv-2026-orals-posters.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

CONCURRENCY = 12
TIMEOUT = 25


def fetch(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return r.text


def parse_poster_page() -> list[tuple[str, str]]:
    """(id, title) from papers.html noscript block."""
    text = fetch(PAPERS_PAGE)
    items = re.findall(
        r'<li><a href="/virtual/2026/poster/(\d+)">(.*?)</a></li>', text, re.S
    )
    return [(pid, clean(html.unescape(t))) for pid, t in items]


def parse_oral_page() -> list[tuple[str, str]]:
    """(id, title) from /events/oral page.  Entries appear twice (card + details)."""
    text = fetch(ORALS_PAGE)
    items = re.findall(
        r'<a href="/virtual/2026/oral/(\d+)"[^>]*>(.*?)</a>', text, re.S
    )
    seen: dict[str, str] = {}
    for pid, t in items:
        title = clean(html.unescape(t))
        if title and title != "View full details" and pid not in seen:
            seen[pid] = title
    return list(seen.items())


def parse_static_json() -> dict[str, dict]:
    """{id: {'title','authors','eventtype'}} from static JSON (first 200)."""
    text = fetch(STATIC_JSON)
    payload = json.loads(text)
    out: dict[str, dict] = {}
    for r in payload.get("results", []):
        authors = "; ".join(
            clean(a.get("fullname", "")) for a in (r.get("authors") or []) if a.get("fullname")
        )
        out[str(r.get("id"))] = {
            "title": clean(r.get("name")),
            "authors": authors,
            "eventtype": clean(r.get("eventtype")),
        }
    return out


def clean(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("\u200b", "").replace("\u2009", " ")
    return re.sub(r"\s+", " ", s).strip()


def detail_authors(text: str) -> str:
    """JSON-LD preferred, .event-organizers fallback."""
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            names = [a.get("name", "") for a in data.get("author", []) if a.get("name")]
            if names:
                return "; ".join(clean(n) for n in names)
        except (json.JSONDecodeError, AttributeError):
            pass
    m2 = re.search(r'class="event-organizers">(.*?)</div>', text, re.S)
    if m2:
        raw = m2.group(1)
        raw = re.sub(r"<[^>]+>", " ", raw)
        names = [clean(x) for x in re.split(r"\u22c5|;|,", raw)]
        names = [n for n in names if n]
        if names:
            return "; ".join(names)
    return ""


def fetch_detail(pid: str, kind: str) -> tuple[str, str]:
    """Returns (pid, authors)."""
    for attempt in (0, 1):
        try:
            text = fetch(f"{VIRTUAL}/{kind}/{pid}")
            return pid, detail_authors(text)
        except Exception as exc:  # noqa: BLE001
            if attempt == 1:
                return pid, f"__ERROR__:{exc}"


FIELDNAMES = [
    "paper_id", "venue", "venue_type", "year", "track", "title", "authors",
    "abstract", "abstract_source_name", "abstract_source_url",
    "abstract_source_tier", "abstract_fetched_at", "doi", "arxiv_id",
    "paper_url", "pdf_url", "source_name", "source_url", "source_tier",
    "verification_status", "list_status", "fetched_at",
]


def collect(max_detail: int = 0) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """Collect ECCV 2026 papers from the official virtual platform.

    Returns (papers, errors) where papers is a list of CSV-row dicts
    (keys == FIELDNAMES, year as str) and errors is a list of (paper_id, message).

    `max_detail` limits how many detail pages are fetched for authors; if 0 it
    falls back to the MAX_DETAIL environment variable (also 0 = fetch all).
    """
    import os as _os
    if not max_detail:
        max_detail = int(_os.environ.get("MAX_DETAIL", "0"))
    t0 = time.time()
    print("[1/5] posters page ...", flush=True)
    posters = parse_poster_page()
    print(f"      posters={len(posters)}", flush=True)

    print("[2/5] orals page ...", flush=True)
    orals = parse_oral_page()
    print(f"      orals={len(orals)}", flush=True)

    # unify: poster list is the canonical unique set.
    # oral page entries with a matching title upgrade that paper's track to "oral".
    # (ECCV 2026: every oral also appears in the poster listing under a different id)
    oral_by_title: dict[str, str] = {}
    for oral_id, title in orals:
        oral_by_title.setdefault(title, oral_id)

    entries: dict[str, dict] = {}
    for pid, title in posters:
        oral_id = oral_by_title.get(title)
        entries[pid] = {"title": title, "track": "oral" if oral_id else "poster",
                        "oral_id": oral_id}
    # any orals not present in poster list get appended as-is
    poster_titles = {e["title"] for e in entries.values()}
    for oral_id, title in orals:
        if title not in poster_titles:
            entries[f"o{oral_id}"] = {"title": title, "track": "oral", "oral_id": oral_id}
    print(f"[3/5] total unique papers={len(entries)} "
          f"(poster={sum(1 for e in entries.values() if e['track']=='poster')}, "
          f"oral={sum(1 for e in entries.values() if e['track']=='oral')})", flush=True)

    # seed authors from static JSON where id overlaps
    print("[3.5] static json (author seed) ...", flush=True)
    static = parse_static_json()
    seeded = 0
    author_map: dict[str, str] = {}
    for pid, meta in static.items():
        if pid in entries and meta.get("authors"):
            author_map[pid] = meta["authors"]
            seeded += 1
    print(f"      seeded authors={seeded}", flush=True)

    # fetch missing authors from detail pages (always via poster/{id}; list page guarantees existence)
    missing = [pid for pid in entries if pid not in author_map]
    if max_detail:
        missing = missing[:max_detail]
    print(f"[4/5] fetching {len(missing)} detail pages (c={CONCURRENCY}) ...", flush=True)
    ok = 0
    errs: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_detail, pid, "poster"): pid for pid in missing}
        done = 0
        for fut in as_completed(futs):
            pid, authors = fut.result()
            done += 1
            if authors.startswith("__ERROR__"):
                errs.append((pid, authors))
            else:
                if authors:
                    ok += 1
                author_map[pid] = authors
            if done % 300 == 0:
                el = time.time() - t0
                print(f"      {done}/{len(missing)} elapsed={el/60:.1f}min ok={ok} err={len(errs)}",
                      flush=True)
    print(f"      done. authors_ok={ok} empty={len(missing)-ok-len(errs)} err={len(errs)}",
          flush=True)

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    papers: list[dict[str, str]] = []
    for pid in sorted(entries, key=lambda p: int(p.lstrip("o"))):
        meta = entries[pid]
        kind = meta["track"]
        authors = author_map.get(pid, "")
        if authors.startswith("__ERROR__"):
            authors = ""  # drop error placeholders
        detail_id = meta["oral_id"] if (kind == "oral" and meta["oral_id"]) else pid
        papers.append({
            "paper_id": pid,
            "venue": "ECCV",
            "venue_type": "conference",
            "year": "2026",
            "track": kind,
            "title": meta["title"],
            "authors": authors,
            "abstract": "",
            "abstract_source_name": "",
            "abstract_source_url": "",
            "abstract_source_tier": "",
            "abstract_fetched_at": "",
            "doi": "",
            "arxiv_id": "",
            "paper_url": f"https://eccv.ecva.net/virtual/2026/{kind}/{detail_id}",
            "pdf_url": "",
            "source_name": "ECVA official virtual platform (eccv.ecva.net)",
            "source_url": PAPERS_PAGE if kind == "poster" else ORALS_PAGE,
            "source_tier": "official",
            "verification_status": "rolling",
            "list_status": "rolling",
            "fetched_at": fetched_at,
        })
    print(f"[5/5] collect done papers={len(papers)} elapsed={time.time()-t0:.0f}s", flush=True)
    return papers, errs


def main() -> int:
    limit = int(__import__("os").environ.get("MAX_DETAIL", "0"))
    papers, errs = collect(max_detail=limit)
    out = OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(papers)
    print(f"WROTE {out} rows={len(papers)}", flush=True)
    if errs:
        print(f"ERRORS ({len(errs)}):", flush=True)
        for pid, msg in errs[:20]:
            print(f"  {pid}: {msg[:120]}", flush=True)
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
