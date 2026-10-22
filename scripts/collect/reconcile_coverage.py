"""Build a frozen coverage report from saved public enumerations; never edits the catalog."""
from __future__ import annotations
import argparse,csv,hashlib,html,json,re,sqlite3,unicodedata,sys
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit,urlunsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
STATUS={'aligned':'已核齐（指定官方清单）','registry_aligned':'登记清单核齐','differences':'存在差异待核对','unverified':'暂无法核验完整性','not_applicable':'不适用（无该届）','supplement':'关联论文补充','out_of_scope':'不在收录范围'}

def title_key(s):
    return re.sub(r'[^\w]','',unicodedata.normalize('NFKC',html.unescape(s)).casefold())

def keys(row):
    result=set()
    # Source-local paper IDs are not comparable across accepted lists and proceedings.
    for k in ('doi',):
        if row.get(k):result.add((k,row[k].strip().casefold()))
    for k in ('paper_url','pdf_url'):
        if row.get(k):
            u=urlsplit(row[k]);host=u.netloc.lower().removeprefix('www.')
            result.add(('url',urlunsplit(('',host,u.path.rstrip('/'),u.query,''))))
    return result

def compare(reference,local):
    index=defaultdict(set);titles=defaultdict(set)
    for i,r in enumerate(local):
        for k in keys(r):index[k].add(i)
        titles[title_key(r['title'])].add(i)
    matched=set();missing=[];ambiguous=[];methods=Counter()
    for r in reference:
        hits=set().union(*(index[k] for k in keys(r))) if keys(r) else set()
        method='identifier'
        if not hits:hits=titles[title_key(r['title'])];method='unique_exact_title'
        if len(hits)==1:matched.update(hits);methods[method]+=1
        elif hits:ambiguous.append(r)
        else:missing.append(r)
    return {'matched_reference_count':sum(methods.values()),'unmatched_reference_count':len(missing),'ambiguous_reference_count':len(ambiguous),'local_only_count':len(local)-len(matched),'match_methods':dict(methods)},missing,ambiguous,[r for i,r in enumerate(local) if i not in matched]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--database',type=Path,default=ROOT/'data/database/catalog.sqlite');ap.add_argument('--output',type=Path,default=ROOT/'data/catalog/reports/coverage_20261021.json');a=ap.parse_args();folder=a.evidence
    from backend.catalog.metrics import corpus_summary
    con=sqlite3.connect(f'file:{a.database.resolve()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    local=defaultdict(list)
    for r in con.execute('SELECT * FROM paper_records'):local[(r['venue'],r['year'])].append(dict(r))
    enums={(r['venue'],r['year']):r for r in json.loads((folder/'enumerations.json').read_text())}
    fetches=json.loads((folder/'fetches.json').read_text())
    # ICLR virtual index contains presentations beyond the main proceedings. Enumerate it as such.
    from lxml import html as lhtml
    for y in range(2023,2027):
        u=f'https://iclr.cc/virtual/{y}/papers.html';f=next((x for x in fetches if x['url']==u and x['status']==200 and not x['browser_verification']),None)
        if f:
            tree=lhtml.fromstring((folder/f['file']).read_bytes());found={}
            for node in tree.xpath(f'//a[contains(@href,"/virtual/{y}/poster/") or contains(@href,"/virtual/{y}/oral/")]'):
                title=' '.join(node.text_content().split());href=node.get('href','');url='https://iclr.cc'+href if href.startswith('/') else href
                if title:found.setdefault(title_key(title),dict(title=title,paper_url=url,paper_id='',doi='',source_url=u))
            if found:enums[('ICLR',y)]={'rows':list(found.values()),'error':None}
    # Reuse the official 3DV schedule parser against the saved response.
    extras=json.loads((folder/'extra_fetches.json').read_text())
    dv=next((x for x in extras if x['url']=='https://3dvconf.github.io/2026/schedule.csv' and x.get('status')==200),None)
    if dv:
        from scripts.collect.update_catalog import three_dv
        class SavedFetch:
            def get(self,url,suffix=''):return (folder/dv['file']).read_bytes()
        enums[('3DV',2026)]={'rows':three_dv(SavedFetch()),'error':None}
    no_editions={('3DV',2023):('2023 届移至 2024 年。','https://3dvconf.github.io/2024/'),('COLM',2023):('首届为 2024 年。','https://colmweb.org/2024/cfp.html'),('NAACL',2023):('官方届次列表无 2023 届。','https://naacl.org/conferences/index.html'),('NAACL',2026):('官方下一届为 2027；不把 ACL 2026 算作 NAACL。','https://2027.naacl.org/')}
    for v,ys in [('ECCV',[2023,2025]),('ICCV',[2024,2026])]:
        for y in ys:no_editions[(v,y)]=('双年会议，无该年度届次。','https://www.thecvf.com/?page_id=413')
    registry={'AIJ','TPAMI','IJCV','TOG','TIP','TKDE','TOIS','TVCG'}
    venues=[tuple(r) for r in con.execute('SELECT DISTINCT venue,venue_type FROM paper_records ORDER BY venue_type,venue')]
    reviews=json.loads((ROOT/'data/labels/publication_review.json').read_text());result=[];detail=[]
    for v,kind in venues:
      for y in range(2023,2027):
        stored=local[(v,y)];entry=enums.get((v,y));ref=entry['rows'] if entry and not entry['error'] else []
        sources=sorted({r['source_url'] for r in stored if r['source_url'].startswith('http')})
        row=dict(venue=v,venue_type=kind,year=y,scope='supplement' if v=='The Annals of Statistics' else 'primary',local_record_count=len(stored),reference_count=None,matched_reference_count=None,unmatched_reference_count=None,ambiguous_reference_count=None,local_only_count=None,missing_author_count=sum(not r['authors'].strip() for r in stored),missing_abstract_count=sum(not r['abstract'].strip() for r in stored),tracks=sorted({r['track'] for r in stored}),status='unverified',reference_kind='publisher_registry' if v in registry else 'official_list',sources=sources[:4],checked_at='2026-10-21',notes='尚未取得可完整枚举并逐条对账的同口径清单；不能由 final 标签推定完整。')
        if v=='The Annals of Statistics':
            row.update(status='supplement' if y==2026 else 'out_of_scope',notes='仅收录 5 篇 ICML 期刊展示对应的正式论文；不采集整刊。')
        elif (v,y) in no_editions:
            note,u=no_editions[(v,y)];assert not stored;row.update(status='not_applicable',notes=note,sources=[u])
        elif ref:
            counts,missing,ambiguous,extra=compare(ref,stored);row.update(counts);row['reference_count']=len(ref)
            row['sources']=sorted({r['source_url'] for r in ref if r.get('source_url')})[:4] or sources[:4]
            row['status']=('registry_aligned' if v in registry else 'aligned') if not missing and not ambiguous and not extra else 'differences'
            row['notes']='指定清单逐条对账；未匹配条目可能为漏收、改名或年份/轨道差异，不能直接导入或删除。'
            if v in registry:
                from scripts.collect.build_catalog import JOURNALS
                row['sources'].insert(0, f'https://api.crossref.org/journals/{JOURNALS[v][0]}/works?filter=from-pub-date:{y}-01-01,until-pub-date:{y}-12-31,type:journal-article&rows=0')
            if v in registry:row['notes']+=' 对照出版社登记的 Crossref journal-article 元数据，未独立核齐出版社全部卷期；登记日期不等同正式发表日期。'
            if v=='ICLR':row['notes']+=' 对照会议展示名单，含非主会展示可能；不作为正式发表证明。'
            if v=='ICML' and y==2026:row['notes']+=' 本地额外 76 条中 73 条为已核验期刊展示，3 条出版身份待核验。'
            for typ,items in [('reference_unmatched',missing),('ambiguous',ambiguous),('local_only',extra)]:
                for item in items:detail.append(dict(venue=v,year=y,difference=typ,paper_id=item.get('paper_id',''),title=item['title'],doi=item.get('doi',''),paper_url=item.get('paper_url','')))
        elif entry:
            row['notes']='未取得可用于对账的完整清单：'+entry['error']+'；预期数量留空。'
        if not ref and (v,y) not in no_editions and v!='The Annals of Statistics':
            attempts=[x for x in extras if x['venue']==v and x['year']==y]
            if attempts:
                row['sources']=[x['url'] for x in attempts]
                row['notes']+=' 官方索引检查结果：'+', '.join(str(x.get('status',x.get('error'))) for x in attempts)+'；未取得经过验证的完整条目集合。'
        if y==2026:row['notes']+=' 2026 为截至检查日的快照，不代表全年完整。'
        result.append(row)
    report=dict(version=a.output.stem.replace('_','-'),cutoff='2026-10-21',cutoff_timezone='Asia/Shanghai',frozen_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),catalog_release_id=con.execute("SELECT release_id FROM data_releases WHERE status='ready' ORDER BY created_at DESC LIMIT 1").fetchone()[0],scope={'primary_conferences':20,'primary_journals':10,'years':[2023,2024,2025,2026],'supplementary_venues':['The Annals of Statistics'],'policy':'冻结现有目录的来源和轨道边界；会议不自动扩展 workshops、Findings、demo 等未列轨道。已存非论文/展示记录保留溯源，不计作新研究论文。期刊按年度元数据清单核验。截止日期是本版检查边界，不承诺所有来源都更新到当天。'},corpus=corpus_summary(con),identity_review_candidates=con.execute('SELECT COUNT(*) FROM entity_review_candidates').fetchone()[0],publication_pending=sum(r['status']=='pending_publication_verification' for r in reviews),status_counts=dict(Counter(r['status'] for r in result)),rows=result)
    dest=a.output;dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    fields=['venue','venue_type','year','scope','status','local_record_count','reference_count','matched_reference_count','unmatched_reference_count','ambiguous_reference_count','local_only_count','missing_author_count','missing_abstract_count','checked_at','tracks','sources','notes']
    with dest.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in result:w.writerow({**r,'status':STATUS[r['status']],'tracks':' | '.join(r['tracks']),'sources':' | '.join(r['sources'])})
    (dest.parent/(dest.stem+'_differences.json')).write_text(json.dumps(detail,ensure_ascii=False,indent=2))
    (dest.parent/(dest.stem+'_input_hashes.json')).write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'data/catalog').glob('*/*/*.csv'))},indent=2))
    print(report['status_counts']);print('Difference candidates',len(detail))
if __name__=='__main__':main()
