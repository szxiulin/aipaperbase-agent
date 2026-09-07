#!/usr/bin/env python3
"""Build auditable paper catalogs from official venue sources.

Official proceedings are the authority.  Hugging Face's ai-conferences parquet
is used only where an official bulk API is unavailable or where an official
title list needs richer metadata.  Every such row is labelled accordingly.
"""

from __future__ import annotations

import argparse
import ast
import csv
from difflib import SequenceMatcher
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from lxml import etree, html


ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = ROOT / "data" / "catalog"
REPORT_ROOT = CATALOG_ROOT / "reports"
HF_PARQUET_URL = (
    "https://huggingface.co/datasets/ai-conferences/all-papers/resolve/"
    "refs%2Fconvert%2Fparquet/default/train/0000.parquet"
)
FETCHED_AT = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

FIELDS = [
    "paper_id", "venue", "venue_type", "year", "track", "title", "authors",
    "abstract", "abstract_source_name", "abstract_source_url",
    "abstract_source_tier", "abstract_fetched_at",
    "doi", "arxiv_id", "paper_url", "pdf_url", "source_name", "source_url",
    "source_tier", "verification_status", "list_status", "fetched_at",
]

PMLR_VOLUMES = {2023: 202, 2024: 235, 2025: 267}
ACL_COLLECTIONS = {
    "ACL": {2023: "2023.acl", 2024: "2024.acl", 2025: "2025.acl", 2026: "2026.acl"},
    "EMNLP": {2023: "2023.emnlp", 2024: "2024.emnlp", 2025: "2025.emnlp", 2026: "2026.emnlp"},
    "NAACL": {2024: "2024.naacl", 2025: "2025.naacl", 2026: "2026.naacl"},
}
ACL_MAIN_VOLUMES = {
    "ACL": {"long", "short", "main"},
    "EMNLP": {"main"},
    "NAACL": {"long", "short"},
}
JMLR_VOLUMES = {2023: 24, 2024: 25, 2025: 26, 2026: 27}

# Crossref is the DOI registration agency used by these publishers.  The ISSN
# route avoids fuzzy title searches and preserves publisher-registered titles,
# authors, issue data, DOI values, and resource links.
JOURNALS = {
    "AIJ": ("0004-3702", "https://www.sciencedirect.com/journal/artificial-intelligence"),
    "TPAMI": ("0162-8828", "https://www.computer.org/csdl/journal/tp"),
    "IJCV": ("0920-5691", "https://link.springer.com/journal/11263"),
    "TOG": ("0730-0301", "https://dl.acm.org/journal/tog"),
    "TIP": ("1057-7149", "https://signalprocessingsociety.org/publications-resources/ieee-transactions-image-processing"),
    "TKDE": ("1041-4347", "https://www.computer.org/csdl/journal/tk"),
    "TOIS": ("1046-8188", "https://dl.acm.org/journal/tois"),
    "TVCG": ("1077-2626", "https://www.computer.org/csdl/journal/tg"),
}

# DBLP provides a stable, enumerable table of contents.  Each row is checked
# for an official DOI or publisher landing page; the CSV keeps both the DBLP
# enumeration source and the publisher URL instead of pretending DBLP is an
# official proceedings host.
DBLP_CONFERENCES = {
    "KDD": "https://dblp.org/db/conf/kdd/kdd{year}.html",
    "SIGIR": "https://dblp.org/db/conf/sigir/sigir{year}.html",
    "WWW": "https://dblp.org/db/conf/www/www{year}.html",
    "ACM MM": "https://dblp.org/db/conf/mm/mm{year}.html",
}

# Current year: determines whether a year counts as "in progress" (rolling).
# Catalogs with year >= CURRENT_YEAR default to rolling until the year elapses, then become final automatically.
CURRENT_YEAR = datetime.now().year

# Early-finalization overrides: within in-progress years, the venue × year entries whose
# official proceedings are confirmed fully published and whose lists no longer change.
# Registration rule: a venue × year may be registered only once its official proceedings
# page is fully published (conference held, volume finalized); conferences/journals not yet
# held or still publishing stay rolling.
VENUE_FINAL_EXCEPTIONS: dict[str, set[int]] = {
    "AAAI": {2026},
    "ACL": {2026},
    "CVPR": {2026},
    "ICLR": {2026},
    "ICML": {2026},
    "WWW": {2026},
    "SIGIR": {2026},
    "MLSys": {2026},
}


def is_rolling(venue: str, year: int) -> bool:
    """Whether the catalog for this venue×year is still subject to change.

    - venue×year already registered as early-finalized → not rolling (final)
    - year not yet elapsed (year >= current year) → rolling
    - all other historical years → not rolling (final)
    """
    if venue in VENUE_FINAL_EXCEPTIONS and year in VENUE_FINAL_EXCEPTIONS[venue]:
        return False
    return year >= CURRENT_YEAR


@dataclass
class Paper:
    paper_id: str
    venue: str
    venue_type: str
    year: int
    track: str
    title: str
    authors: str
    abstract: str = ""
    abstract_source_name: str = ""
    abstract_source_url: str = ""
    abstract_source_tier: str = ""
    abstract_fetched_at: str = ""
    doi: str = ""
    arxiv_id: str = ""
    paper_url: str = ""
    pdf_url: str = ""
    source_name: str = ""
    source_url: str = ""
    source_tier: str = "official"
    verification_status: str = "verified_official"
    list_status: str = "final"
    fetched_at: str = FETCHED_AT


class Fetcher:
    def __init__(self, cache: Path, delay: float = 0.15):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AIPaperbase Agent-catalog/0.1 (research metadata; contact: local-user)"
        })

    def get(self, url: str, suffix: str = ".html") -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest() + suffix
        path = self.cache / key
        if path.exists():
            return path.read_bytes()
        response = self.session.get(url, timeout=90)
        response.raise_for_status()
        path.write_bytes(response.content)
        time.sleep(self.delay)
        return response.content


def clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_authors(value: object) -> str:
    """Normalize authors to '; '-joined names.

    Handles genuine lists/tuples, numpy arrays, and accidentally repr-ed strings
    that survive a parquet round-trip.  The HF ai-conferences parquet stores
    numpy arrays whose str() form is "['A' 'B']" (no commas) — ast.literal_eval
    would silently merge adjacent strings, so quoted-name extraction comes first.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if s.startswith("[") and s.endswith("]"):
            names = re.findall(r"'([^']*)'|\"([^\"]*)\"", s)
            flat = [a or b for a, b in names]
            if flat:
                return "; ".join(clean(x) for x in flat)
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)):
                    return "; ".join(clean(x) for x in parsed)
            except (ValueError, SyntaxError):
                pass
            return clean(s)
        return clean(s)
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return "; ".join(clean(x) for x in value)
    return clean(value)


def clean_abstract(value: object) -> str:
    """Normalize plain text and Crossref/JATS-style markup without inventing text."""
    raw = clean(value)
    if not raw:
        return ""
    if "<" in raw and ">" in raw:
        try:
            raw = text_content(html.fromstring(f"<div>{raw}</div>"))
        except (etree.ParserError, ValueError):
            raw = re.sub(r"<[^>]+>", " ", raw)
    return clean(raw)


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[^\w]+", "", value)


# Block-level tags get a separator space between their text runs; inline tags
# (fixed-case, i, b, sup, sub, ...) must NOT — naive " ".join(itertext()) inserted
# spaces inside titles like <title>O<fixed-case>cto</fixed-case>Tools</title>,
# producing "O cto T ools" (ACL Anthology family, ~2,855 records).
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "td", "th", "table", "section", "article", "blockquote", "pre", "hr",
})


def text_content(node) -> str:
    """Join element text without inventing spaces inside inline markup."""
    if node is None:
        return ""
    chunks: list[str] = []
    for el in node.iter():
        tag = getattr(el, "tag", None)
        is_block = tag in _BLOCK_TAGS
        if is_block:
            chunks.append(" ")
        if el.text:
            chunks.append(el.text)
        if el.tail:
            chunks.append((" " + el.tail) if is_block else el.tail)
    return clean("".join(chunks))


def first(values: list[str]) -> str:
    return clean(values[0]) if values else ""


def next_dds(node, limit: int = 2) -> list:
    """Return direct DD siblings without repeatedly scanning the full document."""
    result = []
    sibling = node.getnext()
    while sibling is not None and len(result) < limit:
        tag = clean(getattr(sibling, "tag", "")).casefold()
        if tag == "dt":
            break
        if tag == "dd":
            result.append(sibling)
        sibling = sibling.getnext()
    return result


def scrape_cvf(fetcher: Fetcher, venue: str, year: int) -> list[Paper]:
    url = f"https://openaccess.thecvf.com/{venue}{year}?day=all"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for dt in tree.xpath('//dt[contains(concat(" ", normalize-space(@class), " "), " ptitle ")]'):
        title_node = first_node(dt.xpath(".//a"))
        if title_node is None:
            continue
        paper_url = urljoin(url, clean(title_node.get("href")))
        paper_id = Path(urlparse(paper_url).path).stem
        dds = next_dds(dt)
        authors = []
        if dds:
            authors = [clean(x) for x in dds[0].xpath('.//input[@name="query_author"]/@value')]
            if not authors:
                authors = [clean(x) for x in dds[0].xpath(".//a/text()")]
        pdf_url = ""
        arxiv_id = ""
        for dd in dds[1:2]:
            for a in dd.xpath(".//a[@href]"):
                label = text_content(a).casefold()
                href = urljoin(url, clean(a.get("href")))
                if label == "pdf":
                    pdf_url = href
                elif label == "arxiv":
                    arxiv_id = href.rstrip("/").split("/")[-1]
        papers.append(Paper(
            paper_id=paper_id, venue=venue, venue_type="conference", year=year,
            track="main", title=text_content(title_node), authors="; ".join(authors),
            arxiv_id=arxiv_id, paper_url=paper_url, pdf_url=pdf_url,
            source_name="CVF Open Access", source_url=url,
            list_status="rolling" if is_rolling(venue, year) else "final",
            verification_status="rolling" if is_rolling(venue, year) else "verified_official",
        ))
    return papers


def scrape_eccv(fetcher: Fetcher, year: int) -> list[Paper]:
    url = "https://www.ecva.net/papers.php"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    marker = f"eccv_{year}"
    for dt in tree.xpath('//dt[contains(concat(" ", normalize-space(@class), " "), " ptitle ")]'):
        a = first_node(dt.xpath(f'.//a[contains(@href,"{marker}")]'))
        if a is None:
            continue
        paper_url = urljoin(url, clean(a.get("href")))
        dds = next_dds(dt)
        authors = text_content(dds[0]).replace("*", "") if dds else ""
        authors = "; ".join(clean(x) for x in authors.split(",") if clean(x))
        pdf_url = doi = ""
        if len(dds) > 1:
            for link in dds[1].xpath(".//a[@href]"):
                label = text_content(link).casefold()
                href = urljoin(url, clean(link.get("href")))
                if label == "pdf":
                    pdf_url = href
                elif label == "doi":
                    doi = href.split("doi.org/")[-1] if "doi.org/" in href else ""
        paper_id = Path(urlparse(pdf_url or paper_url).path).stem
        papers.append(Paper(
            paper_id=paper_id, venue="ECCV", venue_type="conference", year=year,
            track="main", title=text_content(a), authors=authors, doi=doi,
            paper_url=paper_url, pdf_url=pdf_url, source_name="ECVA",
            source_url=url,
        ))
    return papers


def scrape_eccv_virtual(fetcher: Fetcher, year: int) -> list[Paper]:
    """ECCV 2026+ — ECVA official virtual platform (eccv.ecva.net).

    The legacy www.ecva.net/papers.php page was not updated for 2026, and the
    paginated JSON API (/api/miniconf/events) is blocked by the site's WAF
    (403).  The working path (see scrape_eccv_2026.py) is:
      - papers.html  noscript block -> full poster list (id + title)
      - /events/oral page           -> oral list (id + title)
      - detail pages poster|oral/{id} -> authors (JSON-LD, else .event-organizers)

    `fetcher` is accepted for signature compatibility with the other jobs but
    is not used; this collector talks to the virtual platform directly.
    """
    import scrape_eccv_2026 as s2026

    if year != 2026:
        raise ValueError(f"scrape_eccv_virtual only supports 2026 (virtual platform), got {year}")
    rows, errs = s2026.collect()
    if errs:
        raise RuntimeError(f"ECCV {year}: {len(errs)} detail-page fetch errors (first: {errs[0][1][:120]})")
    papers: list[Paper] = []
    for row in rows:
        papers.append(Paper(
            paper_id=row["paper_id"], venue="ECCV", venue_type="conference",
            year=int(row["year"]), track=row["track"], title=row["title"],
            authors=row["authors"], doi="", arxiv_id="",
            paper_url=row["paper_url"], pdf_url="",
            source_name=row["source_name"], source_url=row["source_url"],
            source_tier="official",
            verification_status="rolling" if is_rolling("ECCV", year) else "verified_official",
            list_status="rolling" if is_rolling("ECCV", year) else "final",
            fetched_at=row["fetched_at"],
        ))
    return papers


def scrape_pmlr_icml(fetcher: Fetcher, year: int, volume: int) -> list[Paper]:
    url = f"https://proceedings.mlr.press/v{volume}/"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for block in tree.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " paper ")]'):
        title = first_node(block.xpath('.//p[contains(@class,"title")]'))
        authors = first_node(block.xpath('.//span[contains(@class,"authors")]'))
        abs_url = first(block.xpath('.//p[contains(@class,"links")]//a[normalize-space()="abs"]/@href'))
        pdf_url = first(block.xpath('.//p[contains(@class,"links")]//a[contains(normalize-space(),"PDF")]/@href'))
        if not abs_url:
            continue
        abs_url = urljoin(url, abs_url)
        paper_id = Path(urlparse(abs_url).path).stem
        papers.append(Paper(
            paper_id=paper_id, venue="ICML", venue_type="conference", year=year,
            track="main", title=text_content(title), authors=text_content(authors).replace(",", ";"),
            paper_url=abs_url, pdf_url=urljoin(url, pdf_url), source_name="PMLR",
            source_url=url,
        ))
    return papers


def scrape_neurips(fetcher: Fetcher, year: int) -> list[Paper]:
    url = f"https://proceedings.neurips.cc/paper_files/paper/{year}"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for li in tree.xpath('//ul[contains(@class,"paper-list")]/li'):
        a = first_node(li.xpath('.//a[@title="paper title"]'))
        if a is None:
            continue
        paper_url = urljoin(url, clean(a.get("href")))
        match = re.search(r"/hash/([0-9a-f]+)-Abstract-([^.]+)\.html", paper_url)
        if not match:
            continue
        paper_hash, track_slug = match.groups()
        pdf_url = paper_url.replace("/hash/", "/file/").replace("-Abstract-", "-Paper-").replace(".html", ".pdf")
        track = first_node(li.xpath('.//span[contains(@class,"paper-track-badge")]'))
        papers.append(Paper(
            paper_id=paper_hash, venue="NeurIPS", venue_type="conference", year=year,
            track=text_content(track) or clean(track_slug.replace("_", " ")),
            title=text_content(a),
            authors=text_content(first_node(li.xpath('.//span[contains(@class,"paper-authors")]'))).replace(",", ";"),
            paper_url=paper_url, pdf_url=pdf_url, source_name="NeurIPS Proceedings",
            source_url=url,
        ))
    return papers


def xml_text(node, path: str) -> str:
    item = node.find(path)
    return text_content(item) if item is not None else ""


def scrape_acl(fetcher: Fetcher, venue: str, year: int, collection: str) -> list[Paper]:
    url = f"https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/{collection}.xml"
    root = etree.fromstring(fetcher.get(url, ".xml"))
    allowed = ACL_MAIN_VOLUMES[venue]
    papers: list[Paper] = []
    for volume in root.findall("volume"):
        volume_id = clean(volume.get("id"))
        if volume_id not in allowed:
            continue
        for node in volume.findall("paper"):
            official_id = xml_text(node, "url")
            if not official_id or official_id.endswith(".report"):
                continue
            author_names = []
            for author in node.findall("author"):
                name = clean(" ".join(filter(None, [xml_text(author, "first"), xml_text(author, "last")])))
                if name:
                    author_names.append(name)
            doi = xml_text(node, "doi")
            abstract = clean_abstract(xml_text(node, "abstract"))
            papers.append(Paper(
                paper_id=official_id, venue=venue, venue_type="conference", year=year,
                track=volume_id, title=xml_text(node, "title"), authors="; ".join(author_names),
                abstract=abstract,
                abstract_source_name="ACL Anthology official XML" if abstract else "",
                abstract_source_url=url if abstract else "",
                abstract_source_tier="official" if abstract else "",
                abstract_fetched_at=FETCHED_AT if abstract else "",
                doi=doi, paper_url=f"https://aclanthology.org/{official_id}/",
                pdf_url=f"https://aclanthology.org/{official_id}.pdf",
                source_name="ACL Anthology official XML", source_url=url,
                list_status="rolling" if is_rolling(venue, year) else "final",
                verification_status="rolling" if is_rolling(venue, year) else "verified_official",
            ))
    return papers


def scrape_ijcai(fetcher: Fetcher, year: int) -> list[Paper]:
    url = f"https://www.ijcai.org/proceedings/{year}/"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    current_section = ""
    for element in tree.xpath('//div[contains(@class,"section_title")] | //div[contains(@class,"paper_wrapper")]'):
        classes = set(clean(element.get("class")).split())
        if "section_title" in classes:
            current_section = text_content(element)
            continue
        title = text_content(first_node(element.xpath('./div[contains(@class,"title")]')))
        if not title:
            continue
        excluded = {"Sister Conferences Best Papers", "Doctoral Consortium", "Demo Track"}
        if current_section in excluded or "Abstract Reprint" in title or "Extended Abstract" in title:
            continue
        detail_href = first(element.xpath('.//div[contains(@class,"details")]//a[contains(@href,"/proceedings/")]/@href'))
        pdf_href = first(element.xpath('.//div[contains(@class,"details")]//a[contains(translate(text(),"PDF","pdf"),"pdf")]/@href'))
        if not detail_href:
            continue
        paper_url = urljoin(url, detail_href)
        paper_id = paper_url.rstrip("/").split("/")[-1]
        authors = text_content(first_node(element.xpath('./div[contains(@class,"authors")]'))).replace(",", ";")
        papers.append(Paper(
            paper_id=paper_id, venue="IJCAI", venue_type="conference", year=year,
            track=current_section or "main", title=title, authors=authors,
            doi=f"10.24963/ijcai.{year}/{paper_id}", paper_url=paper_url,
            pdf_url=urljoin(url, pdf_href), source_name="IJCAI Proceedings",
            source_url=url,
        ))
    return papers


def scrape_ijcai_accepted(fetcher: Fetcher, year: int) -> list[Paper]:
    """IJCAI-ECAI — official accepted-papers page (https://{year}.ijcai.org/accepted-papers).

    The proceedings page (www.ijcai.org/proceedings/{year}/) is published only
    after the conference, so for the current edition the conference site's
    accepted list is the authoritative source: a single static page with all
    accepted papers — title, authors, abstract, session/poster schedule and
    official S3 preprint PDF links.

    paper_id: numeric for the main track (matches DOI 10.24963/ijcai.YYYY/N);
    prefixed for special tracks (e.g. AI4G6, DM33, SV1) which have no
    proceedings DOI yet.  track is derived from the leading alphabetic prefix
    ("main" for numeric ids).
    """
    url = f"https://{year}.ijcai.org/accepted-papers"
    text = fetcher.get(url).decode("utf-8")
    papers: list[Paper] = []
    for block in re.findall(r'<li class="ij-paper".*?</li>', text, re.S):
        pid_match = re.search(r'ij-pid">#([^<]*)<', block)
        title_match = re.search(r'<h3 class="ij-ptitle">(.*?)</h3>', block, re.S)
        if not pid_match or not title_match:
            continue
        pid = clean(pid_match.group(1))
        title = clean(re.sub(r"<[^>]+>", "", title_match.group(1)))
        author_names = [
            clean(re.sub(r"<[^>]+>", "", m))
            for m in re.findall(r'class="ij-author">(.*?)</span>', block)
        ]
        authors = "; ".join(x for x in author_names if x)
        abs_match = re.search(r'<div class="ij-abstract">(.*?)</div>', block, re.S)
        abstract = (
            clean(re.sub(r"<[^>]+>", "", abs_match.group(1)))
            if abs_match else ""
        )
        pdf_match = re.search(
            r"(https://ijcai-preprints\.s3\.us-west-1\.amazonaws\.com/"
            rf"{year}/[^\"'<> ]+)",
            block,
        )
        pdf_url = pdf_match.group(1) if pdf_match else ""
        track_match = re.match(r"([A-Za-z]+)", pid)
        track = track_match.group(1) if track_match else "main"
        doi = f"10.24963/ijcai.{year}/{pid}" if track == "main" else ""
        papers.append(Paper(
            paper_id=pid, venue="IJCAI", venue_type="conference", year=year,
            track=track, title=title, authors=authors,
            abstract=abstract,
            abstract_source_name="IJCAI official accepted list" if abstract else "",
            abstract_source_url=url if abstract else "",
            abstract_source_tier="official" if abstract else "",
            abstract_fetched_at=FETCHED_AT if abstract else "",
            doi=doi, arxiv_id="",
            paper_url=f"https://www.ijcai.org/proceedings/{year}/{pid}",
            pdf_url=pdf_url,
            source_name=f"IJCAI official accepted list ({year}.ijcai.org)",
            source_url=url,
            list_status="rolling" if is_rolling("IJCAI", year) else "final",
            verification_status="rolling" if is_rolling("IJCAI", year) else "verified_official",
        ))
    return papers


def scrape_jmlr(fetcher: Fetcher, year: int, volume: int) -> list[Paper]:
    url = f"https://www.jmlr.org/papers/v{volume}/"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for dl in tree.xpath('//div[@id="content"]//dl'):
        title = text_content(first_node(dl.xpath("./dt")))
        abs_href = first(dl.xpath('.//a[normalize-space()="abs"]/@href'))
        pdf_href = first(dl.xpath('.//a[normalize-space()="pdf"]/@href'))
        if not title or not abs_href:
            continue
        paper_url = urljoin(url, abs_href)
        paper_id = Path(urlparse(paper_url).path).stem
        authors_node = first_node(dl.xpath("./dd/b/i"))
        authors = text_content(authors_node).replace(",", ";")
        papers.append(Paper(
            paper_id=paper_id, venue="JMLR", venue_type="journal", year=year,
            track=f"volume {volume}", title=title, authors=authors,
            paper_url=paper_url, pdf_url=urljoin(url, pdf_href), source_name="JMLR",
            source_url=url,
            list_status="rolling" if is_rolling("JMLR", year) else "final",
            verification_status="rolling" if is_rolling("JMLR", year) else "verified_official",
        ))
    return papers


def scrape_tmlr(fetcher: Fetcher) -> dict[int, list[Paper]]:
    url = "https://jmlr.org/tmlr/papers/"
    tree = html.fromstring(fetcher.get(url))
    grouped: dict[int, list[Paper]] = {}
    for item in tree.xpath('//li[contains(concat(" ", normalize-space(@class), " "), " item ")]'):
        title_node = first_node(item.xpath(".//h4/a"))
        details = first_node(item.xpath("./p"))
        if title_node is None or details is None:
            continue
        detail_text = text_content(details)
        year_match = re.search(r"\b(20\d{2})\b", detail_text)
        if not year_match:
            continue
        year = int(year_match.group(1))
        if year < 2023:
            continue
        forum_url = first(details.xpath('.//a[normalize-space()="openreview"]/@href'))
        pdf_url = first(details.xpath('.//a[normalize-space()="pdf"]/@href'))
        paper_id = forum_url.split("id=")[-1] if "id=" in forum_url else Path(urlparse(pdf_url).path).stem
        authors = text_content(first_node(details.xpath(".//i"))).replace(",", ";")
        grouped.setdefault(year, []).append(Paper(
            paper_id=paper_id, venue="TMLR", venue_type="journal", year=year,
            track="continuous publication", title=text_content(title_node), authors=authors,
            paper_url=forum_url, pdf_url=pdf_url, source_name="TMLR official papers list",
            source_url=url,
            list_status="rolling" if is_rolling("TMLR", year) else "final",
            verification_status="rolling" if is_rolling("TMLR", year) else "verified_official",
        ))
    return grouped


def scrape_colm(fetcher: Fetcher, year: int) -> list[Paper]:
    url = (
        f"https://colmweb.org/{year}/AcceptedPapers.html"
        if year < 2026 else "https://colmweb.org/AcceptedPapers.html"
    )
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for paragraph in tree.xpath('//p[.//em and .//a]'):
        links = paragraph.xpath('./a | .//a[contains(@href,"openreview.net/forum")]')
        if not links:
            continue
        link = links[-1]
        title = text_content(link)
        if not title:
            continue
        paper_url = clean(link.get("href"))
        if "openreview.net/forum" in paper_url:
            paper_id = paper_url.split("id=")[-1]
        else:
            digest = hashlib.sha256(normalize_title(title).encode()).hexdigest()[:16]
            paper_id = f"provisional-title-{digest}"
            paper_url = url
        authors = text_content(first_node(paragraph.xpath(".//em"))).replace(",", ";")
        papers.append(Paper(
            paper_id=paper_id, venue="COLM", venue_type="conference", year=year,
            track="main", title=title, authors=authors, paper_url=paper_url,
            source_name="COLM Accepted Papers", source_url=url,
            list_status="rolling" if is_rolling("COLM", year) else "final",
            verification_status="rolling" if is_rolling("COLM", year) else "verified_official",
        ))
    return papers


def date_year(item: dict) -> int | None:
    for field in ("published-print", "published-online", "published", "issued"):
        parts = item.get(field, {}).get("date-parts", [])
        if parts and parts[0]:
            return int(parts[0][0])
    return None


def crossref_journal(
    fetcher: Fetcher, venue: str, year: int, issn: str, official_url: str
) -> list[Paper]:
    endpoint = f"https://api.crossref.org/journals/{issn}/works"
    cursor = "*"
    items: list[dict] = []
    while cursor:
        params = {
            "filter": (
                f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,"
                "type:journal-article"
            ),
            "rows": "1000",
            "cursor": cursor,
            "mailto": "metadata@apexpaperrag.local",
        }
        url = endpoint + "?" + urlencode(params)
        payload = json.loads(fetcher.get(url, ".json"))
        message = payload["message"]
        page = message.get("items", [])
        items.extend(page)
        next_cursor = clean(message.get("next-cursor"))
        if not page or len(page) < 1000 or next_cursor == cursor:
            break
        cursor = next_cursor

    papers: list[Paper] = []
    seen = set()
    for item in items:
        doi = clean(item.get("DOI")).lower()
        title_values = item.get("title") or []
        title = clean(title_values[0] if title_values else "")
        if not doi or not title or doi in seen:
            continue
        seen.add(doi)
        authors = []
        for author in item.get("author") or []:
            name = clean(" ".join(filter(None, [author.get("given"), author.get("family")])))
            if name:
                authors.append(name)
        links = item.get("link") or []
        pdf_url = first([
            clean(link.get("URL")) for link in links
            if "pdf" in clean(link.get("content-type")).casefold()
        ])
        volume, issue = clean(item.get("volume")), clean(item.get("issue"))
        track = ", ".join(filter(None, [f"volume {volume}" if volume else "", f"issue {issue}" if issue else ""]))
        abstract = clean_abstract(item.get("abstract"))
        papers.append(Paper(
            paper_id=doi, venue=venue, venue_type="journal", year=year,
            track=track or "continuous publication", title=title,
            authors="; ".join(authors), doi=doi,
            abstract=abstract,
            abstract_source_name="Crossref publisher-registered metadata" if abstract else "",
            abstract_source_url=f"https://api.crossref.org/works/{doi}" if abstract else "",
            abstract_source_tier="official_plus_secondary" if abstract else "",
            abstract_fetched_at=FETCHED_AT if abstract else "",
            paper_url=f"https://doi.org/{doi}", pdf_url=pdf_url,
            source_name=f"Crossref publisher-registered metadata (ISSN {issn})",
            source_url=official_url, source_tier="official_plus_secondary",
            verification_status="rolling" if is_rolling(venue, year) else "crosschecked",
            list_status="rolling" if is_rolling(venue, year) else "final",
        ))
    return papers


def scrape_dblp_conference(
    fetcher: Fetcher, venue: str, year: int, url_template: str
) -> list[Paper]:
    base_url = url_template.format(year=year)
    urls = [base_url]
    trees: list[tuple[str, object]] = []
    try:
        trees.append((base_url, html.fromstring(fetcher.get(base_url))))
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        for part in (1, 2):
            part_url = base_url.replace(".html", f"-{part}.html")
            try:
                trees.append((part_url, html.fromstring(fetcher.get(part_url))))
                urls.append(part_url)
            except requests.HTTPError as part_exc:
                if part_exc.response is None or part_exc.response.status_code != 404:
                    raise
    if not trees:
        raise ValueError(f"No DBLP proceedings pages found for {venue} {year}")
    papers: list[Paper] = []
    for source_url, tree in trees:
        current_track = "main proceedings"
        elements = tree.xpath(
            '//h2 | //h3 | //h4 | '
            '//li[contains(concat(" ", normalize-space(@class), " "), " entry ") '
            'and contains(concat(" ", normalize-space(@class), " "), " inproceedings ")]'
        )
        for entry in elements:
            if entry.tag in {"h2", "h3", "h4"}:
                current_track = text_content(entry)
                continue
            track_key = current_track.casefold()
            excluded_markers = (
                "keynote", "tutorial", "workshop", "doctoral", "demonstration",
                "demo papers", "demos and videos", "technical demonstrations",
                "panel", "invited talk", "special day", "grand challenge",
                "interactive/digital art", "open source", "companion paper",
                "industrial demonstrations", "expert talks",
            )
            if any(marker in track_key for marker in excluded_markers):
                continue
            title = text_content(first_node(entry.xpath('.//span[contains(concat(" ", normalize-space(@class), " "), " title ")]')))
            if not title or title.casefold().startswith(("front matter", "preface", "proceedings of")):
                continue
            authors = [text_content(node) for node in entry.xpath('.//span[@itemprop="author"]')]
            links = [clean(value) for value in entry.xpath('.//a[@href]/@href')]
            doi_url = first([value for value in links if "doi.org/10." in value])
            doi = doi_url.split("doi.org/", 1)[-1].lower() if doi_url else ""
            publisher_url = doi_url or first([
                value for value in links
                if any(host in value for host in (
                    "dl.acm.org", "ieeexplore.ieee.org", "diglib.eg.org",
                    "proceedings.mlsys.org", "link.springer.com",
                ))
            ])
            key = clean(entry.get("id")) or clean(first(entry.xpath('.//cite/@data-key')))
            paper_id = doi or key or hashlib.sha256(normalize_title(title).encode()).hexdigest()[:20]
            papers.append(Paper(
                paper_id=paper_id, venue=venue, venue_type="conference", year=year,
                track=current_track, title=title, authors="; ".join(authors),
                doi=doi, paper_url=publisher_url or source_url,
                source_name="DBLP table of contents cross-checked by publisher/DOI links",
                source_url=source_url, source_tier="third_party_crosschecked",
                verification_status="rolling" if is_rolling(venue, year) else "crosschecked",
                list_status="rolling" if is_rolling(venue, year) else "final",
            ))
    return papers


def scrape_mlsys(fetcher: Fetcher, year: int) -> list[Paper]:
    url = f"https://proceedings.mlsys.org/paper_files/paper/{year}"
    tree = html.fromstring(fetcher.get(url))
    papers: list[Paper] = []
    for li in tree.xpath('//ul[contains(@class,"paper-list")]/li'):
        anchor = first_node(li.xpath('.//a[contains(@href,"-Abstract-")]'))
        if anchor is None:
            continue
        paper_url = urljoin(url, clean(anchor.get("href")))
        match = re.search(r"/hash/([0-9a-f]+)-Abstract-([^.]+)\.html", paper_url)
        if not match:
            continue
        paper_hash, track_slug = match.groups()
        pdf_url = paper_url.replace("/hash/", "/file/").replace("-Abstract-", "-Paper-").replace(".html", ".pdf")
        papers.append(Paper(
            paper_id=paper_hash, venue="MLSys", venue_type="conference", year=year,
            track=clean(track_slug.replace("_", " ")), title=text_content(anchor),
            authors=text_content(first_node(li.xpath('.//span[contains(@class,"paper-authors")]'))).replace(",", ";"),
            paper_url=paper_url, pdf_url=pdf_url,
            source_name="Proceedings of Machine Learning and Systems", source_url=url,
            verification_status="rolling" if is_rolling("MLSys", year) else "verified_official",
            list_status="rolling" if is_rolling("MLSys", year) else "final",
        ))
    return papers


def hf_official_rows(
    hf: pd.DataFrame, source_venue: str, output_venue: str, year: int,
    official_domains: tuple[str, ...], venue_type: str = "conference",
) -> list[Paper]:
    rows = hf_rows(hf, source_venue, year)
    papers: list[Paper] = []
    for paper in rows:
        joined = " ".join([paper.paper_url, paper.pdf_url])
        if not any(domain in joined for domain in official_domains):
            continue
        paper.venue = output_venue
        paper.venue_type = venue_type
        paper.source_name = f"Hugging Face ai-conferences metadata cross-checked by {output_venue} official links"
        paper.source_tier = "official_plus_secondary"
        paper.verification_status = "rolling" if is_rolling(output_venue, year) else "crosschecked"
        papers.append(paper)
    return papers


def official_virtual_rows(
    fetcher: Fetcher, hf: pd.DataFrame, venue: str, year: int
) -> tuple[list[Paper], dict[str, object]]:
    host = venue.casefold()
    url = f"https://{host}.cc/virtual/{year}/papers.html"
    tree = html.fromstring(fetcher.get(url))
    anchors = tree.xpath(
        f'//a[contains(@href,"/virtual/{year}/poster/") or '
        f'contains(@href,"/virtual/{year}/oral/")]'
    )
    official: dict[str, tuple[str, str, str]] = {}
    for anchor in anchors:
        title = text_content(anchor)
        href = clean(anchor.get("href"))
        if not title or not href:
            continue
        key = normalize_title(title)
        track = "oral" if f"/virtual/{year}/oral/" in href else "poster"
        # A paper can appear in both the poster and oral sections. Prefer oral.
        if key not in official or track == "oral":
            official[key] = (title, urljoin(url, href), track)

    subset = hf[(hf["conference"] == venue) & (hf["year"] == year)]
    secondary: dict[str, dict] = {}
    for row in subset.to_dict("records"):
        secondary.setdefault(normalize_title(clean(row.get("title"))), row)

    # The virtual page also contains journal-to-conference, blog, position-paper,
    # or other presentation tracks in some years.  Therefore it is a validation
    # source, not the scope-defining union.  The HF/OpenReview-derived main-track
    # set remains the base and official-only titles stay in the audit report.
    alias_matches: dict[str, tuple[str, str, str]] = {}
    matched_official_keys = set(set(secondary) & set(official))
    if year < 2026:
        for secondary_key, row in secondary.items():
            if secondary_key in official:
                continue
            source_id = clean(row.get("source_paper_id"))
            candidates = sorted(
                (
                    (SequenceMatcher(None, secondary_key, official_key).ratio(), official_key, value)
                    for official_key, value in official.items()
                ),
                reverse=True,
            )[:5]
            for score, official_key, value in candidates:
                if score < 0.45:
                    continue
                detail = fetcher.get(value[1]).decode("utf-8", errors="replace")
                match = re.search(r"openreview\.net/forum\?id=([A-Za-z0-9_-]+)", detail)
                if match and match.group(1) == source_id:
                    alias_matches[secondary_key] = value
                    matched_official_keys.add(official_key)
                    break

    papers: list[Paper] = []
    matched = 0
    for key, row in secondary.items():
        official_match = official.get(key) or alias_matches.get(key)
        if official_match:
            matched += 1
        paper_id = clean(row.get("source_paper_id") or row.get("paper_id"))
        authors = normalize_authors(row.get("authors"))
        abstract = clean_abstract(row.get("abstract"))
        papers.append(Paper(
            paper_id=paper_id, venue=venue, venue_type="conference", year=year,
            track=clean(row.get("type")) or "main", title=clean(row.get("title")),
            authors=clean(authors), abstract=abstract,
            abstract_source_name=f"Hugging Face ai-conferences/OpenReview ({clean(row.get('source'))})" if abstract else "",
            abstract_source_url=f"https://huggingface.co/datasets/ai-conferences/{venue}{year}" if abstract else "",
            abstract_source_tier="third_party_crosschecked" if abstract else "",
            abstract_fetched_at=FETCHED_AT if abstract else "",
            doi=clean(row.get("doi")),
            arxiv_id=clean(row.get("arxiv_id")), paper_url=clean(row.get("paper_url")),
            pdf_url=clean(row.get("pdf_url")),
            source_name=f"Hugging Face ai-conferences/OpenReview metadata + {venue} official virtual list",
            source_url=url, source_tier="third_party_crosschecked",
            verification_status=(
                "rolling" if is_rolling(venue, year) else
                "crosschecked" if official_match else "needs_review"
            ),
            list_status="rolling" if is_rolling(venue, year) else "final",
        ))
    audit = {
        "venue": venue, "year": year, "official_count": len(official),
        "secondary_count": len(secondary), "matched": matched,
        "official_only": len(official) - len(matched_official_keys),
        "secondary_only": len(secondary) - matched,
        "official_source": url,
    }
    return papers, audit


def aaai_rows(fetcher: Fetcher, hf: pd.DataFrame, year: int) -> tuple[list[Paper], dict[str, object]]:
    volume = year - 1986
    url = f"https://aaai.org/proceeding/aaai-{volume}-{year}/"
    tree = html.fromstring(fetcher.get(url))
    allowed_issues: dict[int, str] = {}
    for anchor in tree.xpath('//a[contains(@href,"ojs.aaai.org/index.php/AAAI/issue/view/")]'):
        label = text_content(anchor)
        match = re.search(r"No\.\s*(\d+):", label)
        if not match:
            continue
        if ("Technical Tracks" in label or "Special Track" in label) and not any(
            excluded in label for excluded in ("Senior Member", "Doctoral", "Demonstration", "Student Abstract")
        ):
            allowed_issues[int(match.group(1))] = label

    base_rows = hf_rows(hf, "AAAI", year)
    papers: list[Paper] = []
    for paper in base_rows:
        match = re.search(r"\.v\d+i(\d+)\.", paper.doi)
        issue = int(match.group(1)) if match else None
        if issue not in allowed_issues:
            continue
        paper.track = allowed_issues[issue]
        paper.source_name = "AAAI official proceedings index + Crossref/Hugging Face metadata"
        paper.source_url = url
        paper.source_tier = "official_plus_secondary"
        paper.verification_status = "rolling" if is_rolling("AAAI", year) else "verified_official"
        papers.append(paper)
    audit = {
        "venue": "AAAI", "year": year, "official_count": "",
        "secondary_count": len(base_rows), "matched": len(papers),
        "official_only": "not_enumerated", "secondary_only": len(base_rows) - len(papers),
        "official_source": url,
    }
    return papers, audit


def first_node(nodes):
    return nodes[0] if nodes else None


def load_hf_fallback(fetcher: Fetcher) -> pd.DataFrame:
    path = fetcher.cache / "ai-conferences-all-papers.parquet"
    if not path.exists():
        path.write_bytes(fetcher.get(HF_PARQUET_URL, ".parquet"))
    columns = [
        "paper_id", "conference", "year", "source_paper_id", "source", "title",
        "authors", "abstract", "paper_url", "pdf_url", "doi", "arxiv_id", "type",
    ]
    frame = pd.read_parquet(path, columns=columns)
    return frame[frame["year"] >= 2023].copy()


def hf_rows(df: pd.DataFrame, venue: str, year: int) -> list[Paper]:
    subset = df[(df["conference"] == venue) & (df["year"] == year)]
    papers = []
    for row in subset.to_dict("records"):
        paper_url = clean(row.get("paper_url"))
        pdf_url = clean(row.get("pdf_url"))
        source = clean(row.get("source"))
        official_domains = {
            "AAAI": ("ojs.aaai.org", "doi.org"),
            "ICLR": ("openreview.net",),
            "COLM": ("openreview.net",),
            "ICML": ("openreview.net",),
        }
        joined = " ".join([paper_url, pdf_url])
        if venue in official_domains and not any(domain in joined for domain in official_domains[venue]):
            continue
        authors = normalize_authors(row.get("authors"))
        abstract = clean_abstract(row.get("abstract"))
        papers.append(Paper(
            paper_id=clean(row.get("source_paper_id") or row.get("paper_id")),
            venue=venue, venue_type="conference", year=year,
            track=clean(row.get("type")) or "main", title=clean(row.get("title")),
            authors=clean(authors), abstract=abstract,
            abstract_source_name=f"Hugging Face ai-conferences ({source})" if abstract else "",
            abstract_source_url=f"https://huggingface.co/datasets/ai-conferences/{venue}{year}" if abstract else "",
            abstract_source_tier="third_party_crosschecked" if abstract else "",
            abstract_fetched_at=FETCHED_AT if abstract else "",
            doi=clean(row.get("doi")),
            arxiv_id=clean(row.get("arxiv_id")), paper_url=paper_url,
            pdf_url=pdf_url, source_name=f"Hugging Face ai-conferences ({source})",
            source_url=f"https://huggingface.co/datasets/ai-conferences/{venue}{year}",
            source_tier="third_party_crosschecked",
            verification_status="crosschecked",
            list_status="rolling" if is_rolling(venue, year) else "final",
        ))
    return papers


def write_catalog(papers: list[Paper]) -> Path:
    if not papers:
        raise ValueError("Refusing to write an empty catalog")
    venue, year, venue_type = papers[0].venue, papers[0].year, papers[0].venue_type
    branch = "journals" if venue_type == "journal" else "conferences"
    path = CATALOG_ROOT / branch / venue / f"{venue.lower()}_{year}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for paper in sorted(papers, key=lambda p: (p.track, p.paper_id)):
            writer.writerow(asdict(paper))
    return path


def validate(papers: list[Paper], path: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    for index, paper in enumerate(papers, 2):
        for field in ("paper_id", "venue", "title", "source_url"):
            if not clean(getattr(paper, field)):
                issues.append({"file": str(path.relative_to(ROOT)), "row": str(index), "issue": f"missing_{field}", "value": ""})
        if paper.paper_id in seen:
            issues.append({"file": str(path.relative_to(ROOT)), "row": str(index), "issue": "duplicate_paper_id", "value": paper.paper_id})
        seen[paper.paper_id] = index
        if paper.doi and not paper.doi.startswith("10."):
            issues.append({"file": str(path.relative_to(ROOT)), "row": str(index), "issue": "invalid_doi", "value": paper.doi})
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path("/tmp/apexpaperrag-catalog-cache"))
    parser.add_argument("--skip-fallback", action="store_true")
    args = parser.parse_args()
    fetcher = Fetcher(args.cache)
    jobs: list[tuple[str, int, callable]] = []

    for year in range(2023, 2027):
        jobs.append(("CVPR", year, lambda y=year: scrape_cvf(fetcher, "CVPR", y)))
    for year in (2023, 2025):
        jobs.append(("ICCV", year, lambda y=year: scrape_cvf(fetcher, "ICCV", y)))
    jobs.append(("ECCV", 2024, lambda: scrape_eccv(fetcher, 2024)))
    jobs.append(("ECCV", 2026, lambda: scrape_eccv_virtual(fetcher, 2026)))
    for year, volume in PMLR_VOLUMES.items():
        jobs.append(("ICML", year, lambda y=year, v=volume: scrape_pmlr_icml(fetcher, y, v)))
    for year in (2023, 2024, 2025, 2026):
        jobs.append(("NeurIPS", year, lambda y=year: scrape_neurips(fetcher, y)))
    for venue, mapping in ACL_COLLECTIONS.items():
        for year, collection in mapping.items():
            jobs.append((venue, year, lambda v=venue, y=year, c=collection: scrape_acl(fetcher, v, y, c)))
    for year in (2023, 2024, 2025):
        jobs.append(("IJCAI", year, lambda y=year: scrape_ijcai(fetcher, y)))
    # 2026 (IJCAI-ECAI joint): proceedings page not published yet; use the
    # official accepted list on the conference site (has abstracts + preprint PDFs).
    jobs.append(("IJCAI", 2026, lambda: scrape_ijcai_accepted(fetcher, 2026)))
    for year, volume in JMLR_VOLUMES.items():
        jobs.append(("JMLR", year, lambda y=year, v=volume: scrape_jmlr(fetcher, y, v)))
    for venue, (issn, official_url) in JOURNALS.items():
        for year in range(2023, 2027):
            jobs.append((
                venue, year,
                lambda v=venue, y=year, i=issn, u=official_url: crossref_journal(fetcher, v, y, i, u),
            ))
    for venue, url_template in DBLP_CONFERENCES.items():
        years = range(2023, 2027)
        for year in years:
            jobs.append((
                venue, year,
                lambda v=venue, y=year, u=url_template: scrape_dblp_conference(fetcher, v, y, u),
            ))
    for year in range(2023, 2027):
        jobs.append(("MLSys", year, lambda y=year: scrape_mlsys(fetcher, y)))

    results: dict[tuple[str, int], list[Paper]] = {}
    cross_source_audits: list[dict[str, object]] = []
    errors = []
    for venue, year, job in jobs:
        try:
            rows = job()
            if rows:
                results[(venue, year)] = rows
                print(f"official {venue} {year}: {len(rows)}", file=sys.stderr)
            else:
                errors.append((venue, year, "empty official source"))
        except Exception as exc:
            errors.append((venue, year, f"{type(exc).__name__}: {exc}"))

    try:
        for year, rows in scrape_tmlr(fetcher).items():
            results[("TMLR", year)] = rows
            print(f"official TMLR {year}: {len(rows)}", file=sys.stderr)
    except Exception as exc:
        errors.append(("TMLR", 0, f"{type(exc).__name__}: {exc}"))

    if not args.skip_fallback:
        try:
            hf = load_hf_fallback(fetcher)
            for year in (2023, 2024, 2025, 2026):
                rows, audit = aaai_rows(fetcher, hf, year)
                cross_source_audits.append(audit)
                results[("AAAI", year)] = rows
                print(f"official+secondary AAAI {year}: {len(rows)}", file=sys.stderr)
            for year in (2023, 2024, 2025, 2026):
                rows, audit = official_virtual_rows(fetcher, hf, "ICLR", year)
                cross_source_audits.append(audit)
                results[("ICLR", year)] = rows
                print(f"official+secondary ICLR {year}: {len(rows)}", file=sys.stderr)
            rows, audit = official_virtual_rows(fetcher, hf, "ICML", 2026)
            cross_source_audits.append(audit)
            results[("ICML", 2026)] = rows
            print(f"official+secondary ICML 2026: {len(rows)}", file=sys.stderr)
            for year in (2024, 2025, 2026):
                rows = scrape_colm(fetcher, year)
                results[("COLM", year)] = rows
                print(f"official COLM {year}: {len(rows)}", file=sys.stderr)
            for year in (2023, 2024, 2025, 2026):
                for source_venue, output_venue, domains in (
                    ("SIGGRAPH", "SIGGRAPH", ("doi.org", "dl.acm.org")),
                    ("SIGGRAPHAsia", "SIGGRAPH Asia", ("doi.org", "dl.acm.org")),
                    ("3DV", "3DV", ("doi.org", "openaccess.thecvf.com", "ieeexplore.ieee.org")),
                ):
                    rows = hf_official_rows(hf, source_venue, output_venue, year, domains)
                    if rows:
                        results[(output_venue, year)] = rows
                        print(f"official+secondary {output_venue} {year}: {len(rows)}", file=sys.stderr)
        except Exception as exc:
            errors.append(("HF", 0, f"{type(exc).__name__}: {exc}"))

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    summary = []
    issues = []
    for (venue, year), papers in sorted(results.items()):
        path = write_catalog(papers)
        current_issues = validate(papers, path)
        issues.extend(current_issues)
        summary.append({
            "venue": venue,
            "year": year,
            "rows": len(papers),
            "unique_paper_ids": len({p.paper_id for p in papers}),
            "with_doi": sum(bool(p.doi) for p in papers),
            "with_pdf_url": sum(bool(p.pdf_url) for p in papers),
            "source_tier": papers[0].source_tier,
            "list_status": papers[0].list_status,
            "validation_issues": len(current_issues),
            "file": str(path.relative_to(ROOT)),
        })

    with (REPORT_ROOT / "catalog_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(summary[0]) if summary else ["venue", "year"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(summary)
    with (REPORT_ROOT / "validation_issues.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["file", "row", "issue", "value"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(issues)
    with (REPORT_ROOT / "cross_source_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["venue", "year", "official_count", "secondary_count", "matched", "official_only", "secondary_only", "official_source"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(cross_source_audits)
    with (REPORT_ROOT / "collection_errors.json").open("w", encoding="utf-8") as handle:
        json.dump([{"venue": v, "year": y, "error": e} for v, y, e in errors], handle, ensure_ascii=False, indent=2)

    by_doi: dict[str, list[Paper]] = {}
    for papers in results.values():
        for paper in papers:
            if paper.doi:
                by_doi.setdefault(paper.doi.casefold(), []).append(paper)
    duplicate_rows = []
    for doi, papers in sorted(by_doi.items()):
        venues = sorted({paper.venue for paper in papers})
        if len(venues) < 2:
            continue
        duplicate_rows.append({
            "doi": doi,
            "venues": "; ".join(venues),
            "years": "; ".join(str(value) for value in sorted({paper.year for paper in papers})),
            "title": papers[0].title,
            "download_policy": "download_once_by_doi",
        })
    with (REPORT_ROOT / "cross_venue_duplicates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["doi", "venues", "years", "title", "download_policy"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(duplicate_rows)

    print(
        f"wrote {len(summary)} catalogs, {sum(x['rows'] for x in summary)} rows, "
        f"{len(issues)} validation issues, {len(duplicate_rows)} cross-venue DOI aliases"
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
