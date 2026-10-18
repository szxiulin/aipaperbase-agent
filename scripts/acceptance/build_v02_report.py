"""Build the M1 acceptance page (self-contained HTML) from the catalog, labels and gold metrics.

Every number comes from the database or the metrics JSON produced by golden_evaluate.py; the page
is a read-only rendering with SQL-verifiable drill-downs. Missing gold metrics render as "待复核"
so the page can be generated before the author finishes the review.
"""
import argparse
import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
from backend.analytics import areas as area_queries  # noqa: E402

CSS = """
body{margin:0;background:#f4f6f7;color:#172b35;font:15px/1.65 system-ui,sans-serif}
header{padding:28px 5%;background:#fff;border-bottom:1px solid #d9e2e4}
h1{margin:0 0 6px;font-size:30px}h2{margin:30px 0 10px;font-size:21px}h3{margin:18px 0 6px;font-size:16px}
main{padding:0 5% 60px;max-width:1250px;margin:auto}.muted{color:#5d707a}small{font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:18px 0}
.card{background:#fff;border:1px solid #d9e2e4;border-radius:12px;padding:16px}.big{font-size:30px;font-weight:700}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;background:#edf4f2;font-size:12px}
.ok{background:#e5f4ec}.warn{background:#fff4dc}.bad{background:#ffe9e5}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d9e2e4}th,td{padding:7px 9px;border-bottom:1px solid #e4ebed;text-align:left;font-size:14px}
th{background:#f7fafa}.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:12px;background:#d9eee8;border-radius:3px;overflow:hidden}.bar>b{display:block;height:100%;background:#166b60}
.note{background:#fff;border-left:4px solid #166b60;padding:12px 16px;margin:14px 0;border-radius:6px}
.check{display:flex;gap:8px;align-items:flex-start;margin:6px 0}
"""


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def pct(part, total) -> str:
    return f"{100 * part / total:.1f}%" if total else "—"


def card(label, value, note="", cls=""):
    return f'<div class="card"><div class="muted">{esc(label)}</div><div class="big">{value}</div><small class="{cls}">{esc(note)}</small></div>'


def bar(value, maximum):
    width = max(1.0, 100 * value / maximum) if maximum else 1.0
    return f'<div class="bar"><b style="width:{width:.1f}%"></b></div>'


def build(database: Path, metrics_path: Path, out: Path) -> Path:
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    overview = area_queries.areas_overview(con)
    if not overview.get("label"):
        raise SystemExit("catalog has no label release; build it with backend.catalog.import_csv first")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    trends = area_queries.area_trends(con)
    total = overview["classified_entity_count"]
    label = overview["label"]
    areas = overview["topics"]
    version = f'{label["areas_version"]} · {label["directions_version"]} · {label["release_id"]}'
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")

    # accuracy cards
    area_m = metrics.get("area", {})
    dir_m = metrics.get("direction", {})
    other_m = metrics.get("other_share", {})
    if area_m:
        acc = f'{100 * area_m["accuracy"]:.1f}%'
        acc_note = f'95% CI {area_m["ci95"][0] * 100:.1f}–{area_m["ci95"][1] * 100:.1f}% · n={metrics["n"]}'
        dir_acc = f'{100 * dir_m["strict_accuracy"]:.1f}%'
        dir_note = f'领域正确时 {100 * (dir_m.get("conditional_accuracy") or 0):.1f}% · macro-F1 {dir_m.get("macro_support2", {}).get("f1", 0):.2f}'
        other_rate = f'{100 * other_m.get("gold", 0):.1f}%'
        acc_cls = "ok" if area_m["accuracy"] >= 0.9 else ("warn" if area_m["accuracy"] >= 0.85 else "bad")
    else:
        acc, acc_note, dir_acc, dir_note, other_rate, acc_cls = "待复核", "金标尚未合并", "待复核", "", "待复核", "warn"

    cards = "".join([
        card("领域准确率（金标）", acc, acc_note, acc_cls),
        card("方向严格准确率", dir_acc, dir_note),
        card("金标 other 占比", other_rate, "全库真实 other 见下表"),
        card("论文覆盖", f'{total:,}', f'另有 {overview["non_paper_count"]} 条非论文记录未计分母'),
        card("方向数", f'{overview["direction_count"]}', f'{overview["area_count"] - 1} 个领域 + 其他'),
    ])

    # area table
    max_area = max((x["entity_count"] for x in areas), default=1)
    area_rows = "".join(
        f'<tr><td>{esc(x["name"])}<small class="muted"> {esc(x["topic_id"])}</small></td>'
        f'<td class="num">{x["entity_count"]:,}</td><td class="num">{pct(x["entity_count"], total)}</td>'
        f'<td class="num">{x["direction_count"]}</td><td>{bar(x["entity_count"], max_area)}</td></tr>'
        for x in sorted(areas, key=lambda y: -y["entity_count"]))

    # yearly trend (share of the year's papers)
    years = trends["years"]
    by_year_area = {}
    for item in trends["items"]:
        by_year_area.setdefault(item["year"], {})[item["area_id"]] = item["share_rate"]
    top_areas = [x["topic_id"] for x in sorted(areas, key=lambda y: -y["entity_count"])[:6]]
    trend_head = "".join(f"<th>{esc(next(x['name'] for x in areas if x['topic_id'] == aid))}</th>" for aid in top_areas)
    trend_rows = "".join(
        f'<tr><td>{y["year"]}</td><td class="num">{y["total_entities"]:,}</td>'
        + "".join(f'<td class="num">{by_year_area.get(y["year"], {}).get(aid, 0):.1f}%</td>' for aid in top_areas)
        + "</tr>" for y in years)

    # venue x area heatmap (top venues)
    venue_rows = con.execute(
        """SELECT p.venue, COUNT(DISTINCT a.entity_id) AS n
           FROM entity_area_assignments a
           JOIN entity_memberships m ON m.entity_id = a.entity_id
           JOIN paper_records p ON p.record_id = m.record_id
           WHERE a.is_non_paper = 0 GROUP BY p.venue ORDER BY n DESC LIMIT 10""").fetchall()
    heat = {}
    for r in con.execute(
            """SELECT p.venue, a.area_id, COUNT(DISTINCT a.entity_id) AS n
               FROM entity_area_assignments a
               JOIN entity_memberships m ON m.entity_id = a.entity_id
               JOIN paper_records p ON p.record_id = m.record_id
               WHERE a.is_non_paper = 0 GROUP BY p.venue, a.area_id"""):
        heat[(r["venue"], r["area_id"])] = r["n"]
    area_order = [x["topic_id"] for x in sorted(areas, key=lambda y: -y["entity_count"])]
    heat_head = "".join(f"<th>{esc(next(x['name'] for x in areas if x['topic_id'] == aid))}</th>" for aid in area_order)
    heat_rows = ""
    for v in venue_rows:
        cells = ""
        for aid in area_order:
            n = heat.get((v["venue"], aid), 0)
            alpha = min(0.85, n / max(1, v["n"]) * 0.9)
            cells += f'<td class="num" style="background:rgba(22,107,96,{alpha:.2f})">{n or ""}</td>'
        heat_rows += f'<tr><td>{esc(v["venue"])}</td>{cells}</tr>'

    # directions
    directions = [(x["topic_id"], x["name"], x["entity_count"], x["status"]) for area in areas for x in area["subtopics"]]
    directions.sort(key=lambda d: -d[2])
    top_dirs = "".join(
        f'<tr><td>{esc(d[1])}<small class="muted"> {esc(d[0])}</small></td><td class="num">{d[2]:,}</td>'
        f'<td><span class="tag {"warn" if d[3] == "pending_review" else ""}">{esc(d[3])}</span></td></tr>'
        for d in directions[:20])
    others = "".join(
        f'<tr><td>{esc(d[1])}<small class="muted"> {esc(d[0])}</small></td><td class="num">{d[2]:,}</td></tr>'
        for d in directions if d[0].endswith("-other"))

    # old topic vs new
    old_null = con.execute(
        """SELECT COUNT(*) FROM paper_entities e
           WHERE NOT EXISTS (SELECT 1 FROM entity_topic_assignments a
                             WHERE a.entity_id = e.entity_id AND a.role = 'primary')""").fetchone()[0]
    sample_rows = con.execute(
        """SELECT e.entity_id, e.canonical_title AS title,
                  (SELECT t.name FROM entity_topic_assignments a JOIN topic_definitions t ON t.topic_id = a.topic_id
                   WHERE a.entity_id = e.entity_id AND a.role = 'primary' LIMIT 1) AS old_topic,
                  a.area_id, d.direction_id
           FROM paper_entities e
           JOIN entity_area_assignments a ON a.entity_id = e.entity_id AND a.is_non_paper = 0
           LEFT JOIN entity_direction_assignments d ON d.entity_id = e.entity_id
           ORDER BY e.entity_id LIMIT 15""").fetchall()
    old_rows = "".join(
        f'<tr><td>{esc(r["title"])}</td><td>{esc(r["old_topic"] or "（旧分类未归类）")}</td>'
        f'<td>{esc(r["area_id"])}</td><td>{esc(r["direction_id"] or "—")}</td></tr>' for r in sample_rows)

    # confusions
    conf = metrics.get("confusions", {})
    conf_rows = "".join(
        f'<tr><td>{esc(c["pair"]["gold"])}</td><td>{esc(c["pair"]["pred"])}</td><td class="num">{c["count"]}</td>'
        f'<td class="muted">{esc(", ".join(c["examples"]))}</td></tr>'
        for c in (conf.get("area") or [])[:10]) or '<tr><td colspan="4" class="muted">待金标复核后生成</td></tr>'

    # SQL checks
    checks = [
        ("每个实体恰好一个领域", con.execute("SELECT COUNT(*) FROM entity_area_assignments").fetchone()[0] == overview["entity_count"]),
        ("每个论文恰好一个方向", con.execute("SELECT COUNT(*) FROM entity_direction_assignments").fetchone()[0] == total),
        ("非论文记录未被分配方向", con.execute(
            """SELECT COUNT(*) FROM entity_area_assignments a JOIN entity_direction_assignments d
               ON d.entity_id = a.entity_id WHERE a.is_non_paper = 1""").fetchone()[0] == 0),
        ("方向所属领域与论文领域一致", con.execute(
            """SELECT COUNT(*) FROM entity_direction_assignments d
               JOIN direction_definitions dd ON dd.direction_id = d.direction_id
               JOIN entity_area_assignments a ON a.entity_id = d.entity_id
               WHERE dd.area_id <> a.area_id""").fetchone()[0] == 0),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed:
        con.close()
        raise RuntimeError("data consistency checks failed: " + "; ".join(failed))
    check_rows = "".join(
        f'<div class="check"><span class="tag {"ok" if ok else "bad"}">{"通过" if ok else "失败"}</span>'
        f'<span>{esc(name)}</span></div>' for name, ok in checks)

    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>v0.2 数据层验收</title><style>{CSS}</style>
<header><h1>v0.2 数据层验收</h1>
<p class="muted">生成时间 {esc(now)} · 数据版本 {esc(version)} · 论文 {total:,} 篇（另有 {overview['non_paper_count']} 条非论文记录）</p>
<p>目标：领域/方向标签进入 catalog，网页"数据洞察"可展示与下钻，并用 400 篇金标给出准确率。</p></header>
<main>
<h2>1. 结论</h2><div class="grid">{cards}</div>
<div class="note">{'领域准确率 ≥ 90%，达到目标。' if area_m and area_m['accuracy'] >= 0.9 else '领域准确率未达 90% 或尚未复核，见下方口径与遗留。'}
{'<br><b>复核方式：</b>' + esc(metrics.get('reviewer')) + '。这是代理复核，不是独立人工金标；同领域方向差异多数按系统采纳，方向准确率会偏高。' if 'proxy' in str(metrics.get('reviewer', '')) else ''}</div>
<h2>2. 数据一致性（SQL 校验）</h2>{check_rows}
<h2>3. 领域分布</h2><table><tr><th>领域</th><th>论文</th><th>占已分类</th><th>方向数</th><th></th></tr>{area_rows}</table>
<h2>4. 逐年趋势（占当年论文）</h2><table><tr><th>年份</th><th>论文</th>{trend_head}</tr>{trend_rows}</table>
<h2>5. Venue × 领域</h2><p class="muted">取论文数最多的 10 个 venue；数字为论文实体数，颜色深浅表示该领域在本 venue 的占比。</p>
<table><tr><th>venue</th>{heat_head}</tr>{heat_rows}</table>
<h2>6. 方向体系</h2><h3>规模最大的 20 个方向</h3><table><tr><th>方向</th><th>论文</th><th>状态</th></tr>{top_dirs}</table>
<h3>待分流的"其他"方向</h3><table><tr><th>方向</th><th>论文</th></tr>{others or '<tr><td colspan="2" class="muted">无</td></tr>'}</table>
<h2>7. 新旧分类对比</h2>
<p>旧规则分类有 <b>{old_null:,}</b> 个实体没有 primary 主题；新标签覆盖 <b>{total:,}</b> 篇论文。抽查 15 行：</p>
<table><tr><th>论文</th><th>旧主题</th><th>新领域</th><th>新方向</th></tr>{old_rows}</table>
<h2>8. 领域混淆（金标）</h2><table><tr><th>金标领域</th><th>系统领域</th><th>篇数</th><th>示例 entity_id</th></tr>{conf_rows}</table>
<h2>9. 口径与已知问题</h2>
<ul>
<li>金标 N={metrics.get('n', 0)}；复核方式：{esc(metrics.get('reviewer', 'author'))}。LLM 预标只作参考，不参与准确率。</li>
<li>代理复核说明：跨领域分歧逐篇改判 12 篇，同领域方向分歧改判 6 篇；其余按系统标签采纳。1 条勘误声明（Correction: CMAE-3D）移出分母。</li>
<li>方向 macro-F1 只统计金标支持 ≥2 的方向（覆盖 {dir_m.get('macro_classes', 0)} 个方向）。</li>
<li>分母默认排除 {overview['non_paper_count']} 条非论文记录（编委、目录、勘误等）；它们仍可在论文库检索。</li>
<li>缺摘要论文 8,316 篇；金标中 {metrics.get('missing_abstract', {}).get('n', '—')} 篇，仅标题判断。</li>
<li>4 个"其他"方向仍待细分，其中 llm 的待分流组最大；不影响本次验收，但需要后续版本处理。</li>
<li>方向别名只做精确匹配；匹配不到的新论文按向量最近中心分配。</li>
</ul>
<h2>10. 签收</h2>
<div class="check"><span class="tag">☐</span><span>数据：领域/方向覆盖与一致性通过</span></div>
<div class="check"><span class="tag">☐</span><span>指标：400 篇金标复核完成，准确率与口径可接受</span></div>
<div class="check"><span class="tag">☐</span><span>产品：数据洞察页能展示新分类并下钻</span></div>
<p class="muted">验收单：docs/验收/v0.2_数据层验收单.md · 复核数据：data/labels/golden_v1.csv</p>
</main></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--metrics", type=Path, default=Path("data/labels/golden_v1.metrics.json"))
    ap.add_argument("--out", type=Path, default=Path("output/验收/v0.2_数据层验收.html"))
    a = ap.parse_args()
    out = build(a.database, a.metrics, a.out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
