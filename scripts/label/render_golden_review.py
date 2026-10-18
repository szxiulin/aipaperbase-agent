"""Render the offline gold-set review page (one card per sampled paper) from sample + prelabel.

The page is self-contained: no network, no writes outside the output path. It stores progress in
localStorage and exports a JSON file that golden_merge.py validates. The author's edits become the
gold label; the prelabel and the system label stay in the file for comparison.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

TEMPLATE = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>400 篇金标复核</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f7;color:#172b35;font:15px/1.6 system-ui,sans-serif}
header{padding:20px 4%;background:#fff;border-bottom:1px solid #d9e2e4;position:sticky;top:0;z-index:5}
h1{margin:0 0 6px;font-size:24px}button,select,input,textarea{font:inherit;border:1px solid #b8cbd0;border-radius:7px;padding:6px 9px;background:#fff;color:inherit}
button{cursor:pointer}button.primary{background:#166b60;color:#fff}button.on{background:#d9eee8;border-color:#166b60}
main{padding:18px 4%;max-width:1100px}.card{background:#fff;border:1px solid #d9e2e4;border-radius:12px;padding:16px;margin-bottom:14px}
.card.done{border-left:5px solid #166b60}.muted{color:#5d707a}small{font-size:13px}
.tag{display:inline-block;padding:2px 7px;background:#edf4f2;border-radius:6px;margin:2px;font-size:12px}
.tag.bad{background:#ffe9e5}.tag.good{background:#e5f4ec}.abstract{white-space:pre-wrap;font-size:14px;color:#445b66;max-height:260px;overflow:auto;border-top:1px dashed #d9e2e4;padding-top:8px;margin-top:8px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px}
#bar{position:sticky;bottom:0;background:#fff;border-top:1px solid #d9e2e4;padding:10px 4%;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#notice{color:#166b60;min-height:22px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style>
<header><h1>400 篇金标复核</h1><p class="muted">默认已填入 LLM 预标。请逐篇确认；不确定时先看摘要，再改领域/方向。可先点“只看分歧”，再回来补确认。</p>
<div class="row"><strong id="progress"></strong><button id="acceptAll">全部接受预标</button><button id="filterDisagree">只看分歧（<span id="disagreeCount"></span>）</button><button id="filterPending">只看未确认</button><button id="filterMissing">只看缺摘要</button><button id="filterAll" class="on">全部</button>
<input id="search" placeholder="搜索标题 / entity_id" style="min-width:240px"></div><div id="notice"></div></header>
<main id="cards"></main>
<div id="bar"><span id="saved" class="muted"></span><button id="export" class="primary">导出复核 JSON</button><span class="muted">导出后把文件路径告诉我即可；正式分类尚未改变。</span></div>
<script id="payload" type="application/json">__DATA__</script><script>
'use strict';const data=JSON.parse(document.getElementById('payload').textContent),key='golden-review-'+data.sample_sha;
let review={};try{review=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){}
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function rec(id){if(review[id])return review[id];const it=data.items.find(x=>x.entity_id===id);return {area:it.pred_area||it.system_area,direction:it.pred_direction||it.system_direction,notes:'',touched:false}}
function save(){try{localStorage.setItem(key,JSON.stringify(review))}catch(e){$('notice').textContent='本地存储失败，请立即导出。'}}
function set(id,field,value){review[id]={...rec(id),[field]:value,touched:true};save();render()}
function isDisagree(it){return it.pred_area!==it.system_area||it.pred_direction!==it.system_direction}
function dirOptions(area,selected){return (data.directions[area]||[]).map(d=>`<option value="${esc(d.id)}" ${d.id===selected?'selected':''}>${esc(d.name_zh)} · ${esc(d.id)}</option>`).join('')}
let filter='all';
function render(){
 const q=$('search').value.trim().toLowerCase();let shown=0;
 $('cards').innerHTML=data.items.filter(it=>{
   if(filter==='disagree'&&!isDisagree(it))return false;
   if(filter==='pending'&&rec(it.entity_id).touched)return false;
   if(filter==='missing'&&it.has_abstract)return false;
   return !q||(it.title+' '+it.entity_id).toLowerCase().includes(q);
 }).map(it=>{const r=rec(it.entity_id);shown++;return `<article class="card ${r.touched?'done':''}" id="${esc(it.entity_id)}">
  <div><strong>${esc(it.title)}</strong></div><div class="muted"><small>${esc(it.entity_id)} · ${esc(it.venue)} ${it.year} · ${esc(it.system_area)} / ${esc(it.system_direction)}</small></div>
  <div class="row">${isDisagree(it)?'<span class="tag bad">与系统不一致</span>':'<span class="tag good">与系统一致</span>'}
   <span class="tag">预标：${esc(it.pred_area||'—')} / ${esc(it.pred_direction||'—')}</span>
   <span class="tag">系统：${esc(it.system_area)} / ${esc(it.system_direction)}</span>
   ${it.has_abstract?'':'<span class="tag bad">缺摘要</span>'}
   ${it.abstract_source && it.abstract_source !== 'catalog' ? `<span class="tag good">摘要补全：${esc(it.abstract_source)}</span>` : ''}
   <button data-act="accept" data-id="${esc(it.entity_id)}" class="${r.touched?'':'primary'}">接受预标</button>
   <button data-act="system" data-id="${esc(it.entity_id)}">采用系统</button>
   <button data-act="confirm" data-id="${esc(it.entity_id)}" class="${r.touched?'on':''}">${r.touched?'已确认':'确认当前'}</button></div>
  <div class="grid"><label>领域<select data-id="${esc(it.entity_id)}" data-field="area">${data.areas.map(a=>`<option value="${esc(a.id)}" ${a.id===r.area?'selected':''}>${esc(a.name_zh)} · ${esc(a.id)}</option>`).join('')}</select></label>
  <label>方向<select data-id="${esc(it.entity_id)}" data-field="direction">${dirOptions(r.area,r.direction)}</select></label></div>
  <label class="muted"><small>备注（可选）</small><input data-id="${esc(it.entity_id)}" data-field="notes" value="${esc(r.notes||'')}" style="width:100%"></label>
  <details><summary>查看摘要${it.abstract_source && it.abstract_source !== 'catalog' ? '（补全：'+esc(it.abstract_source)+'）' : ''}</summary><div class="abstract">${esc(it.abstract||'此论文缺摘要，仅有标题。')}</div></details></article>`}).join('')||'<p class="muted">没有符合筛选的论文。</p>';
 const done=Object.values(review).filter(r=>r.touched).length;
 $('progress').textContent=`已确认 ${done} / ${data.items.length}`;$('disagreeCount').textContent=data.items.filter(isDisagree).length;
 $('cards').querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>{const id=b.dataset.id,it=data.items.find(x=>x.entity_id===id);
  if(b.dataset.act==='accept')review[id]={area:it.pred_area||it.system_area,direction:it.pred_direction||it.system_direction,notes:rec(id).notes||'',touched:true};
  if(b.dataset.act==='system')review[id]={area:it.system_area,direction:it.system_direction,notes:rec(id).notes||'',touched:true};
  if(b.dataset.act==='confirm')review[id]={...rec(id),touched:true};save();render()});
 $('cards').querySelectorAll('[data-field]').forEach(e=>{const ev=e.tagName==='SELECT'?'change':'input';e.addEventListener(ev,()=>{const id=e.dataset.id,f=e.dataset.field;
   if(f==='area'){const it=data.items.find(x=>x.entity_id===id);review[id]={...rec(id),area:e.value,direction:(data.directions[e.value]||[{}])[0].id||'',touched:true};save();render();return}
   set(id,f,e.value)})});
 $('saved').textContent=done===data.items.length?'全部已确认，可以导出。':'请继续确认；导出需要 400 篇全部确认。';
}
$('search').oninput=render;
$('acceptAll').onclick=()=>{if(!confirm('把 400 篇全部设为当前值（预标）并标记已确认？之后可逐篇改。'))return;data.items.forEach(it=>{review[it.entity_id]={area:it.pred_area||it.system_area,direction:it.pred_direction||it.system_direction,notes:rec(it.entity_id).notes||'',touched:true}});save();render()};
$('filterAll').onclick=()=>{filter='all';render()};$('filterDisagree').onclick=()=>{filter='disagree';render()};
$('filterPending').onclick=()=>{filter='pending';render()};$('filterMissing').onclick=()=>{filter='missing';render()};
$('export').onclick=()=>{const missing=data.items.filter(it=>!rec(it.entity_id).touched);if(missing.length){$('notice').textContent=`还有 ${missing.length} 篇未确认（可点“只看未确认”）。`;return}
 let out={schema:'golden-review-v1',sample_sha:data.sample_sha,exported_at:new Date().toISOString(),reviews:{}};
 data.items.forEach(it=>{const r=rec(it.entity_id);out.reviews[it.entity_id]={area:r.area,direction:r.direction,notes:r.notes||'',touched:true}});
 const b=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),u=URL.createObjectURL(b),a=document.createElement('a');
 a.href=u;a.download='golden-review-'+data.sample_sha.slice(0,8)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
 $('notice').textContent='已导出。正式分类未改变。'};
render();
</script></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=Path, default=Path("data/analyses/golden_20261018/sample.csv"))
    ap.add_argument("--prelabel", type=Path, default=Path("data/analyses/golden_20261018/prelabel.csv"))
    ap.add_argument("--catalog", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--areas", type=Path, default=Path("config/areas.json"))
    ap.add_argument("--directions", type=Path, default=Path("config/directions.json"))
    ap.add_argument("--supplement", type=Path, default=Path("data/analyses/golden_20261018/abstract_supplement.csv"),
                    help="abstracts fetched for catalog-missing papers (exact identifiers only)")
    ap.add_argument("--out", type=Path, default=Path("data/analyses/golden_20261018/金标复核.html"))
    a = ap.parse_args()

    areas = [{"id": x["id"], "name_zh": x["zh"]} for x in json.loads(a.areas.read_text(encoding="utf-8"))["areas"]]
    directions = json.loads(a.directions.read_text(encoding="utf-8"))["directions"]
    by_area: dict[str, list[dict]] = {}
    for d in directions:
        by_area.setdefault(d["area"], []).append({"id": d["direction_id"], "name_zh": d["name_zh"]})
    for v in by_area.values():
        v.sort(key=lambda x: x["id"])

    with a.sample.open(encoding="utf-8", newline="") as f:
        sample = list(csv.DictReader(f))
    with a.prelabel.open(encoding="utf-8", newline="") as f:
        pre = {r["entity_id"]: r for r in csv.DictReader(f)}
    supplement = {}
    if a.supplement.exists():
        with a.supplement.open(encoding="utf-8", newline="") as f:
            supplement = {r["entity_id"]: r for r in csv.DictReader(f) if r.get("abstract")}
    con = sqlite3.connect(f"file:{a.catalog}?mode=ro", uri=True)
    items = []
    for row in sample:
        eid = row["entity_id"]
        meta = con.execute("""SELECT e.canonical_title, r.abstract, r.venue, r.year
                              FROM paper_entities e JOIN paper_records r ON r.record_id = e.canonical_record_id
                              WHERE e.entity_id = ?""", (eid,)).fetchone()
        p = pre.get(eid, {})
        abstract = (meta[1] if meta else "") or ""
        source = "catalog" if abstract.strip() else ""
        if eid in supplement:
            abstract, source = supplement[eid]["abstract"], supplement[eid]["source"]
        items.append({"entity_id": eid, "title": meta[0] if meta else "", "abstract": abstract,
                      "abstract_source": source,
                      "venue": meta[2] if meta else "", "year": meta[3] if meta else 0,
                      "system_area": row["area"], "system_direction": row["direction_id"],
                      "pred_area": p.get("pred_area", ""), "pred_direction": p.get("pred_direction", ""),
                      "has_abstract": bool(abstract.strip())})
    con.close()
    sample_sha = hashlib.sha256("\n".join(sorted(r["entity_id"] for r in sample)).encode()).hexdigest()
    data = {"sample_sha": sample_sha, "items": items, "areas": areas, "directions": by_area}
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html)
    supplemented = sum(1 for x in items if x.get("abstract_source") not in ("", "catalog"))
    print(f"wrote {a.out} ({len(items)} papers, {supplemented} supplemented abstracts, "
          f"{sum(1 for x in items if not x['has_abstract'])} still without abstract)")


if __name__ == "__main__":
    main()
