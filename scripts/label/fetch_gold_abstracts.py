"""Fetch abstracts for gold papers that the catalog has no abstract for (exact identifiers only).

Sources: OpenAlex by DOI, and the ECCV virtual page by its own URL. Title must match the catalog
title (normalized) before an abstract is accepted; no fuzzy title-only search. Writes a supplement
CSV used by render_golden_review.py; it does not modify the catalog or any label file.
"""
import argparse
import csv
import difflib
import html
import json
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

UA = "AIPaperbaseAgent/0.2 (gold review supplement; local research project)"


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").lower()
    return re.sub(r"[^a-z0-9]+", "", value)


def same_title(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def openalex_abstract(doi: str) -> tuple[str, str, str] | None:
    data = json.loads(get("https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(doi)))
    title = data.get("title") or data.get("display_name") or ""
    inverted = data.get("abstract_inverted_index")
    if not inverted:
        return None
    positions = {}
    for word, places in inverted.items():
        for place in places:
            positions[place] = word
    abstract = " ".join(positions[i] for i in sorted(positions))
    return title, abstract, data.get("doi") or ""


def eccv_abstract(url: str) -> tuple[str, str, str] | None:
    page = get(url)
    title_match = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else ""
    body = re.search(r'class="abstract-text-inner">(.*?)</div>', page, re.S)
    if not body:
        return None
    text = re.sub(r"<[^>]+>", " ", body.group(1))
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return title, text, url


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=Path, default=Path("data/analyses/golden_20261018/sample.csv"))
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--out", type=Path, default=Path("data/analyses/golden_20261018/abstract_supplement.csv"))
    a = ap.parse_args()

    with a.sample.open(encoding="utf-8", newline="") as f:
        missing = [r["entity_id"] for r in csv.DictReader(f) if r["has_abstract"] == "0"]
    con = sqlite3.connect(f"file:{a.catalog}?mode=ro", uri=True)
    rows = {}
    for eid in missing:
        rows[eid] = con.execute(
            """SELECT e.canonical_title, r.doi, r.paper_url FROM paper_entities e
               JOIN paper_records r ON r.record_id = e.canonical_record_id WHERE e.entity_id = ?""", (eid,)).fetchone()
    con.close()

    done = set()
    if a.out.exists():
        with a.out.open(encoding="utf-8", newline="") as f:
            done = {r["entity_id"] for r in csv.DictReader(f) if r.get("abstract")}
    fields = ["entity_id", "source", "source_url", "matched_title", "abstract", "fetched_at"]
    new_file = not a.out.exists()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    fetched = skipped = 0
    with a.out.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        for eid, (title, doi, url) in rows.items():
            if eid in done:
                continue
            result = source = None
            try:
                if doi:
                    hit = openalex_abstract(doi)
                    if hit and same_title(title, hit[0]):
                        result, source = hit[1], "openalex"
                if not result and "eccv.ecva.net" in (url or ""):
                    hit = eccv_abstract(url)
                    if hit and same_title(title, hit[0]):
                        result, source = hit[1], "eccv"
            except Exception as exc:  # network / parse failures are not fatal
                print(f"  {eid}: {type(exc).__name__}: {exc}")
            if result and len(result) >= 200:
                w.writerow({"entity_id": eid, "source": source, "source_url": url or doi or "",
                            "matched_title": title, "abstract": result,
                            "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")})
                f.flush()
                fetched += 1
                print(f"  + {source:8s} {title[:70]}")
            else:
                skipped += 1
            time.sleep(0.4)
    print(f"fetched {fetched}, skipped {skipped}, total missing {len(rows)} -> {a.out}")


if __name__ == "__main__":
    main()
