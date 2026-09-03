#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAlex completeness audit script (L1 counts + L2 DOI difference, asymmetric judgment).

Usage (from the project root):
  .venv/bin/python scripts/audit/audit_openalex.py --l1                # L1: count comparison for 10 journals × 2023-2026
  .venv/bin/python scripts/audit/audit_openalex.py --l2 --venue TIP --year 2026   # L2: DOI difference for a given venue×year
  .venv/bin/python scripts/audit/audit_openalex.py --l2-all            # L2 full: all 10 journals × years, quick difference-A check
  .venv/bin/python scripts/audit/audit_openalex.py --all --venue TIP --year 2026  # all

Judgment principle (asymmetric, see docs/模块设计/10_科研API工具_OpenAlex.md §4):
- DOIs that OpenAlex has but the local library lacks → worth investigating, likely a collector miss → go to the "to collect" list
- DOIs the local library has but OpenAlex lacks → likely OpenAlex index lag / caliper difference; record only, do not classify as a library defect
- Caliper difference: OpenAlex publication_year uses the online-first year, the library uses the Crossref volume/issue year (TIP 2026: OA 633 vs library 663)
- Therefore the L2 "library has, OA lacks" check uses existence in **any year** (not limited to publication_year) to avoid caliper false positives
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

# Allow running directly as a script (sys.path points at the project root)
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.catalog.database import DEFAULT_DATABASE, connect  # noqa: E402

API_BASE = "https://api.openalex.org"
TIMEOUT = 30.0
MAX_RETRIES = 5
BULK_DOI_LIMIT = 100  # OpenAlex filter=doi:a|b|c accepts at most 100 at a time

# 10 journals (ISSN exact match, consistent with the mapping in backend/agent/openalex_tools.py)
JOURNALS: dict[str, str] = {
    "AIJ": "0004-3702", "TPAMI": "0162-8828", "IJCV": "0920-5691", "TOG": "0730-0301",
    "TIP": "1057-7149", "TKDE": "1041-4347", "TOIS": "1046-8188", "TVCG": "1077-2626",
    "JMLR": "1532-4435", "TMLR": "2835-8856",
}
YEARS = [2023, 2024, 2025, 2026]


def _mailto() -> str:
    return os.environ.get("OPENALEX_MAILTO", "you@example.com")


def _api_key() -> str:
    return os.environ.get("OPENALEX_API_KEY", "")


def _get(params: dict[str, str]) -> dict:
    """GET OpenAlex (exponential backoff on 429). Returns (status, data) — network failures raise an exception."""
    params = dict(params)
    params.setdefault("mailto", _mailto())
    headers = {"User-Agent": f"aipaperbase-audit mailto:{_mailto()}"}
    key = _api_key()
    if key:
        headers["api-key"] = key
    attempt = 0
    while True:
        try:
            resp = httpx.get(f"{API_BASE}/works", params=params, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
                attempt += 1
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"网络错误: {exc}") from exc


# ---------- L1: count comparison ----------

def l1_count_journal(issn: str, year: int) -> int:
    data = _get({
        "per-page": "1",
        "filter": f"locations.source.issn:{issn},publication_year:{year}",
    })
    return int(data.get("meta", {}).get("count", 0))


def _local_count(conn, venue: str, year: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM paper_records WHERE venue = ? AND year = ?", (venue, year)
    ).fetchone()["n"]


def run_l1(conn, years: list[int] | None = None) -> list[dict]:
    years = years or YEARS
    rows: list[dict] = []
    print(f"\n=== L1：期刊计数对比（OpenAlex publication_year 口径 vs 库内卷期年口径） ===")
    print(f"{'venue':<6}{'year':<6}{'OpenAlex':>9}{'本地库':>9}{'差异':>7}  判定")
    for venue, issn in JOURNALS.items():
        for year in years:
            try:
                oa = l1_count_journal(issn, year)
            except RuntimeError as exc:
                print(f"{venue:<6}{year:<6}  OpenAlex 查询失败: {exc}")
                continue
            local = _local_count(conn, venue, year)
            diff = oa - local
            verdict = "一致" if diff == 0 else ("OA 多" if diff > 0 else "库多")
            rows.append({"venue": venue, "year": year, "openalex": oa, "local": local, "diff": diff})
            print(f"{venue:<6}{year:<6}{oa:>9}{local:>9}{diff:>+7}  {verdict}")
            time.sleep(0.15)  # polite rate limiting
    return rows


# ---------- L2: DOI difference ----------

def _oa_dois_by_year(issn: str, year: int) -> set[str]:
    """Fetch all DOIs (publication_year caliper) for a given journal and year from OpenAlex (cursor pagination)."""
    dois: set[str] = set()
    cursor = "*"
    page = 0
    while cursor:
        params = {
            "per-page": "100",
            "filter": f"locations.source.issn:{issn},publication_year:{year}",
            "select": "doi",
            "cursor": cursor,
        }
        data = _get(params)
        for w in data.get("results") or []:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").strip().lower()
            if doi:
                dois.add(doi)
        page += 1
        cursor = data.get("meta", {}).get("next_cursor")
        if page > 50:  # guard against infinite loops
            break
        time.sleep(0.1)
    return dois


def _bulk_oa_existence(dois: list[str]) -> set[str]:
    """Bulk query: whether the local library's DOIs exist in OpenAlex (any year, regardless of publication_year).
    Returns the set of DOIs that OpenAlex has indexed. 100 at a time, joined with OR."""
    found: set[str] = set()
    normalized = sorted({d.strip().lower() for d in dois if d and d.strip()})
    for i in range(0, len(normalized), BULK_DOI_LIMIT):
        batch = normalized[i:i + BULK_DOI_LIMIT]
        data = _get({
            "per-page": "1",
            "filter": "doi:" + "|".join(batch),
        })
        # The hit count is the number already indexed in that batch; specific DOIs would require paging, so here just use the count for existence checks.
        found_count = int(data.get("meta", {}).get("count", 0))
        # If the whole batch is fully hit, add directly; otherwise confirm one by one (the batch is small, and the per-single-entity free endpoint is more accurate).
        if found_count == len(batch):
            found.update(batch)
        elif found_count > 0:
            for d in batch:
                try:
                    one = _get({"filter": f"doi:{d}", "per-page": "1", "select": "doi"})
                    if int(one.get("meta", {}).get("count", 0)) > 0:
                        found.add(d)
                except RuntimeError:
                    pass
                time.sleep(0.05)
        time.sleep(0.1)
    return found


def run_l2(conn, venue: str, year: int, a_only: bool = False) -> dict:
    """L2 DOI difference. When a_only=True, only difference A (miss-collection candidates) is done, skipping Bulk B (saves time; intended for the full mode)."""
    print(f"\n=== L2：DOI 差集（{venue} {year}，非对称判定{', a_only' if a_only else ''}） ===")
    issn = JOURNALS.get(venue)
    if not issn:
        print(f"venue {venue} 不在 10 期刊清单（仅支持期刊，会议在 OpenAlex 按届拆分无法精确拉取）")
        return {}
    # The local library's DOIs for this venue×year
    local_rows = conn.execute(
        "SELECT DISTINCT lower(trim(doi)) AS doi FROM paper_records WHERE venue = ? AND year = ? AND doi <> ''",
        (venue, year),
    ).fetchall()
    local_dois = {r["doi"] for r in local_rows}
    # OpenAlex DOIs for this journal and year (publication_year)
    print(f"拉取 OpenAlex {venue} {year} DOI 列表…")
    oa_dois = _oa_dois_by_year(issn, year)
    print(f"  OpenAlex({venue} {year}) DOI 数: {len(oa_dois)}；本地库 DOI 数: {len(local_dois)}")

    # Difference A: DOIs OpenAlex has but the local library (this venue×year) lacks — needs a second check for presence anywhere in the library
    oa_only = oa_dois - local_dois
    # Difference B: DOIs the local library has but OpenAlex (this year) lacks — judged by existence in any year
    local_only_this_year = local_dois - oa_dois

    result: dict = {
        "venue": venue, "year": year,
        "openalex_dois": len(oa_dois), "local_dois": len(local_dois),
        "oa_only": sorted(oa_only), "local_only_any_year_absent": [],
    }

    # Difference A second check: whether it is absent from the library in any year (the real miss-collection candidate)
    print(f"\n【非对称判定 A】OpenAlex 有、本地库 {venue} {year} 没有: {len(oa_only)} 条")
    truly_missing: list[str] = []
    if oa_only:
        placeholders = ",".join("?" * len(oa_only))
        found_elsewhere = {
            r["doi"] for r in conn.execute(
                f"SELECT DISTINCT lower(trim(doi)) AS doi FROM paper_records WHERE doi <> '' AND lower(trim(doi)) IN ({placeholders})",
                list(oa_only),
            ).fetchall()
        }
        truly_missing = sorted(oa_only - found_elsewhere)
        for d in sorted(oa_only)[:10]:
            tag = "（库内其他年份/venue 已收录，属年份口径差）" if d in found_elsewhere else "【疑似漏采】"
            print(f"  + {d} {tag}")
        if len(oa_only) > 10:
            print(f"  … 共 {len(oa_only)} 条")
    result["oa_only_truly_missing"] = truly_missing

    # Difference B: the local library has it but OpenAlex doesn't for this year → existence in any year
    print(f"\n【非对称判定 B】本地库有、OpenAlex {venue} {year} 没有: {len(local_only_this_year)} 条")
    if local_only_this_year and not a_only:
        print(f"  用 Bulk DOI 查询这 {len(local_only_this_year)} 条在 OpenAlex 任意年份的存在性…")
        found = _bulk_oa_existence(list(local_only_this_year))
        absent = sorted(local_only_this_year - found)
        result["local_only_any_year_absent"] = absent
        for d in absent[:10]:
            print(f"  - {d}  【OpenAlex 未收录（索引滞后，不定性为库缺陷）】")
        if len(absent) > 10:
            print(f"  … 共 {len(absent)} 条")
        if found:
            print(f"  （其中 {len(found)} 条 OpenAlex 存在，仅年份口径差异，正常）")
    elif local_only_this_year:
        print(f"  （a_only：跳过 Bulk 校验；此类差异属口径差/索引滞后，见 L1 与试点 L2）")
    return result


def run_l2_all(conn, years: list[int] | None = None) -> None:
    """L2 full: all 10 journals × given years, only a quick difference-A (miss-collection candidates) check."""
    years = years or YEARS
    print(f"\n=== L2 全量：差集 A（OpenAlex 有而库无 = 漏采候选），{len(JOURNALS)} 期刊 × {len(years)} 年 ===")
    rows: list[dict] = []
    for venue, issn in JOURNALS.items():
        for year in years:
            try:
                oa_count = l1_count_journal(issn, year)
            except RuntimeError as exc:
                print(f"{venue} {year}: OA 查询失败 {exc}")
                continue
            if oa_count == 0:
                print(f"{venue} {year}: OA 计数 0（该 ISSN 未收录），跳过")
                rows.append({"venue": venue, "year": year, "oa": 0, "missing": 0})
                continue
            try:
                res = run_l2(conn, venue, year, a_only=True)
            except RuntimeError as exc:
                print(f"{venue} {year}: L2 失败 {exc}")
                continue
            rows.append({
                "venue": venue, "year": year,
                "oa": res.get("openalex_dois", 0),
                "missing": len(res.get("oa_only_truly_missing", [])),
            })
            time.sleep(0.2)
    print(f"\n=== L2 全量汇总（差集 A = 真正漏采候选） ===")
    total = 0
    for r in rows:
        total += r["missing"]
        flag = "跳过(OA 未收录)" if r["oa"] == 0 else ("** 疑似漏采 **" if r["missing"] else "OK")
        print(f"{r['venue']:<6}{r['year']:<6} OA={r['oa']:<6} 差集A={r['missing']:<4} {flag}")
    print(f"\n【总计】疑似漏采候选: {total} 条")


# ---------- entry point ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAlex 完整性审计（非对称判定）")
    parser.add_argument("--l1", action="store_true", help="L1：10 期刊 × 2023-2026 计数对比")
    parser.add_argument("--l2", action="store_true", help="L2：指定 venue×year DOI 差集")
    parser.add_argument("--l2-all", action="store_true", help="L2 全量：全部 10 期刊 × 年份，差集 A 快速检查")
    parser.add_argument("--all", action="store_true", help="L1 + L2")
    parser.add_argument("--venue", default="TIP", help="L2 期刊（默认 TIP）")
    parser.add_argument("--year", type=int, default=2026, help="L2 年份（默认 2026）")
    args = parser.parse_args()

    if not (args.l1 or args.l2 or args.l2_all or args.all):
        parser.print_help()
        return

    conn = connect(DEFAULT_DATABASE, read_only=True)
    try:
        if args.l1 or args.all:
            run_l1(conn)
        if args.l2 or args.all:
            run_l2(conn, args.venue, args.year)
        if args.l2_all:
            run_l2_all(conn)
    finally:
        conn.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
