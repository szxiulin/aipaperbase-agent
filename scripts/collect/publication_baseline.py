"""Freeze official publication inventories, separate from accepted/program lists.

Reads saved public evidence and a read-only catalog. Never edits paper records.
"""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,re,sqlite3,sys
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urljoin
from lxml import html
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.collect.reconcile_coverage import keys,title_key
from scripts.collect import build_catalog as catalog
CUTOFF='2026-10-21'
EXCLUDED=re.compile(r'^(program chairs[’\x27]? report on peer review|front matter\b|erratum\b|corrigendum\b|correction (to|of)\b)',re.I)
FORMAL={'CVPR','ICCV','ECCV','ICML','IJCAI','NeurIPS','MLSys','ACL','EMNLP','NAACL','JMLR','TMLR'}

def eligible_title(title):return not EXCLUDED.search(title.strip())

def publication_identity(row):
    return hashlib.sha256((row['venue']+'\0'+str(row['year'])+'\0'+(row.get('doi') or row.get('paper_url') or row['paper_id'])).encode()).hexdigest()

def run(database, evidence, output):
    old=ROOT/'data/analyses/coverage_20261021';previous=json.loads((ROOT/'data/catalog/reports/coverage_20261021_round3.json').read_text())
    conn=sqlite3.connect(f'file:{database.resolve()}?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    local=[dict(r) for r in conn.execute('SELECT * FROM paper_records')];groups=defaultdict(list);global_keys=defaultdict(dict);global_titles=defaultdict(dict)
    for r in local:
        groups[(r['venue'],r['year'])].append(r)
        for k in keys(r):global_keys[k][r['record_id']]=r
        global_titles[title_key(r['title'])][r['record_id']]=r
    enums={(r['venue'],r['year']):r['rows'] for r in json.loads((old/'enumerations.json').read_text()) if r['rows'] and r['venue'] in FORMAL}
    # Accepted lists/virtual schedules are deliberately not admitted here.
    aaai=json.loads((evidence/'aaai_issues.json').read_text());issue_checks={}
    for y in range(2023,2027):
        issues=[x for x in aaai['issues'] if x['year']==y];issue_checks[y]={'archive_complete':aaai['archive_complete'],'issues':len(issues),'errors':sum(bool(x['error']) for x in issues)}
        if issues and aaai['archive_complete'] and not any(x['error'] for x in issues):
            track_numbers={int(x['track'].rsplit(' ',1)[1]) for x in issues}
            if track_numbers != set(range(1,max(track_numbers)+1)):raise ValueError('AAAI technical track enumeration has a gap')
            entries=[]
            for x in issues:
                m=re.search(r'(\d{4}-\d{2}-\d{2})',x['publication_text']);published=m[1] if m else ''
                if not published or published>CUTOFF:raise ValueError('AAAI issue date absent or beyond cutoff')
                entries.extend({**r,'publication_date':published,'date_precision':'day'} for r in x['rows'])
            enums[('AAAI',y)]=entries
    # New formal ICLR repository and MLSys 2026, parsed with the proceedings format.
    fetches=json.loads((evidence/'proceedings_fetches.json').read_text())
    for f in fetches:
        m=re.search(r'proceedings\.iclr\.cc/paper_files/paper/(202[4-6])$',f['url'])
        if not m or f['status']!=200:continue
        y=int(m[1]);t=html.fromstring((evidence/f['file']).read_bytes());entries=[]
        for li in t.xpath('//ul[contains(@class,"paper-list")]/li'):
            a=li.xpath('.//a[@title="paper title"]')
            if not a:continue
            a=a[0];url=urljoin(f['url'],a.get('href'));authors=' '.join(' '.join(li.xpath('.//span[contains(@class,"paper-authors")]//text()')).split())
            entries.append({'paper_id':Path(url).stem,'venue':'ICLR','venue_type':'conference','year':y,'track':'main','title':' '.join(a.text_content().split()),'authors':authors.replace(',',';'),'paper_url':url,'pdf_url':'','doi':'','source_name':'ICLR official proceedings','source_url':f['url']})
        if entries:enums[('ICLR',y)]=entries
    probe=json.loads((evidence/'probe.json').read_text())
    f=next(x for x in probe if x['url']=='https://proceedings.mlsys.org/paper_files/paper/2026')
    if f.get('status')==200:
        class Saved:
            def get(self,url,suffix=''):return (evidence/f['file']).read_bytes()
        import dataclasses
        entries=[dataclasses.asdict(x) for x in catalog.scrape_mlsys(Saved(),2026)]
        if entries:enums[('MLSys',2026)]=entries
    springer_path=evidence/'springer_complete.json'
    springer_excluded=[]
    if springer_path.exists():
        springer=json.loads(springer_path.read_text());online=[x for x in springer if x['url'].endswith('/online-first')]
        all_issues=[x for x in springer if '/volumes-and-issues/' in x['url']]
        for y in range(2023,2027):
            if not all_issues or not online or not online[0]['complete'] or not all(x['complete'] for x in all_issues):continue
            entries={};valid=True
            for issue in all_issues+online:
                for item in issue['rows']:
                    item={**item};item['volume_year']=item['year']+2000 if item['year']<100 else item['year']
                    if not item['publication_date']:valid=False;continue
                    item['year']=int(item['publication_date'][:4])
                    if item['year']!=y or item['publication_date']>CUTOFF:continue
                    if item['document_type'] in ['Correction','News','EditorialNotes','Editorial','Editorial Notes','Obituary','Guest Editorial']:
                        springer_excluded.append({**item,'exclusion_reason':'Publisher non-research document type: '+item['document_type']});continue
                    entries.setdefault(item['doi'],item)
            if valid:enums[('IJCV',y)]=list(entries.values())
    excluded=springer_excluded;items=[];manifest=[];differences=[]
    for oldrow in previous['rows']:
        venue,y=oldrow['venue'],oldrow['year']
        if venue=='The Annals of Statistics':continue
        row={**oldrow};stored=groups[(venue,y)];reference=enums.get((venue,y));notes=[]
        row.update(local_record_count=len(stored), missing_author_count=sum(not x['authors'].strip() for x in stored), missing_abstract_count=sum(not x['abstract'].strip() for x in stored), reference_kind='formal_publication',reference_count=None,matched_reference_count=None,unmatched_reference_count=None,local_only_count=None,ambiguous_reference_count=None,known_published_count=0,status='unverified')
        if oldrow['status']=='not_applicable':row['status']='not_applicable';notes=[oldrow['notes']]
        elif reference is not None:
            seen=set();clean=[]
            for ref in reference:
                if not eligible_title(ref['title']):excluded.append({**ref,'exclusion_reason':'Published non-research material outside scope'});continue
                key=publication_identity(ref)
                if key in seen:raise ValueError('Duplicate publication key in official inventory')
                seen.add(key);clean.append(ref)
            counts={'matched_reference_count':0,'unmatched_reference_count':0,'ambiguous_reference_count':0};matched_local=set()
            for ref in clean:
                hits={x['record_id']:x for k in keys(ref) for x in global_keys[k].values()}
                if not hits:hits=dict(global_titles[title_key(ref['title'])])
                within={k:x for k,x in hits.items() if x['venue']==venue and x['year']==y}
                if len(within)==1:disposition='matched';counts['matched_reference_count']+=1;matched_local.update(within)
                elif within:disposition='ambiguous';counts['ambiguous_reference_count']+=1
                elif hits:disposition='existing_elsewhere';counts['unmatched_reference_count']+=1
                else:disposition='missing_candidate';counts['unmatched_reference_count']+=1
                entry={**ref,'publication_key':publication_identity(ref),'evidence_kind':'official_published_journal_index' if ref['venue_type']=='journal' else 'official_proceedings','publication_date':ref.get('publication_date',''),'date_precision':ref.get('date_precision',''),'observed_by_cutoff':True,'identity_status':disposition,'matched_record_ids':list(within if disposition=='matched' else hits)}
                items.append(entry)
                if disposition!='matched':differences.append(entry)
            row.update(counts,reference_count=len(clean),local_only_count=len(stored)-len(matched_local),known_published_count=len(clean))
            row['status']='aligned' if not counts['unmatched_reference_count'] and not counts['ambiguous_reference_count'] else 'differences'
            row['sources']=sorted({r['source_url'] for r in clean});notes=['已枚举指定正式出版目录并逐条对账；本地额外来源记录可含录用、展示或非论文，不据此删除。','按截至检查日可见的出版目录冻结，未提供具体首发日期的留空；2026 不代表全年完整。']
            if venue=='IJCV':notes.append('枚举出版社卷期及 Online First；按首次正式上线年对账，卷期年另外保存，排除官方标注的非研究文献。')
            if venue=='AAAI':notes.append(f"完整遍历官方归档并读取 {issue_checks[y]['issues']} 个 Technical Tracks 卷；不扩展其他轨道。")
        else:
            if venue in catalog.JOURNALS:notes=['只有 DOI 登记清单及个别文章页，尚未枚举出版社全部卷期和在线发表文章；正式出版总量未知。']
            elif venue=='COLM':notes=['2024–2025 现有录用名单不能替代正式出版清单；2026 官方论文集入口明确标为 pre-proceedings，暂不作为正式出版底表。']
            elif venue=='ICLR':notes=['现有展示名单未用作正式出版证明；新论文集网站未列出 2023 卷，需用 OpenReview 等官方归档另核。']
            else:notes=['尚未取得可完整枚举的正式出版清单；现有录用名单、日程或二级索引只保留为线索，不据此判断已正式出版。']
            if venue=='IJCV':notes.append('出版社卷期入口可访问，完整卷期解析和 Online First 对账尚未完成，不能标为已核齐。')
            if venue in ['AIJ','TOG','TOIS','ACM MM','KDD','SIGIR','WWW','SIGGRAPH','SIGGRAPH Asia']:notes.append('出版社入口访问受限或未取得完整可枚举目录；缺口保留。')
        row['notes']=' '.join(notes);manifest.append(row)
    # Individually verified publisher pages prove membership, never whole-journal coverage.
    reviews=json.loads((ROOT/'data/labels/publication_review.json').read_text())
    for r in reviews:
        if r['status'] not in ('verified_early_access', 'verified_publisher_index'):
            continue
        k = (r['venue'], r['year'])
        stored = next(x for x in groups[k] if x['paper_id'] == r['paper_id'])
        ref = {key: stored[key] for key in ['paper_id', 'venue', 'venue_type', 'year',
            'track', 'title', 'authors', 'doi', 'paper_url', 'source_url']}
        if r.get('publisher_export'):
            # Publisher year and first-online year are separate; never rewrite IDs.
            ref['catalog_year'] = stored['year']
            ref['publisher_year'] = r['publisher_year']
            ref['year'] = int(r['publication_date'][:4])
            k = (r['venue'], ref['year'])
            nonpaper = conn.execute("""SELECT a.is_non_paper
                FROM entity_memberships m JOIN entity_area_assignments a USING(entity_id)
                WHERE m.record_id=?""", (stored['record_id'],)).fetchone()
            if nonpaper and nonpaper[0]:
                excluded.append({**ref, 'exclusion_reason': 'Existing verified non-research label; official publisher export confirms publication'})
                continue
        entry = {**ref, 'publication_key': publication_identity(ref),
            'evidence_kind': 'official_published_journal_index' if r.get('publisher_export') else 'individual_official_page',
            'publication_date': r['publication_date'], 'date_precision': 'day',
            'observed_by_cutoff': True, 'publication_status': r['publication_status'],
            'identity_status': 'matched', 'matched_record_ids': [stored['record_id']]}
        if any(x['publication_key'] == entry['publication_key'] for x in items):
            continue
        items.append(entry)
        unit = next(x for x in manifest if (x['venue'], x['year']) == k)
        unit['known_published_count'] += 1
        if r.get('publisher_export'):
            unit['sources'] = sorted(set(unit['sources'] + [r['publisher_export']['query_url']]))
            note = '另已核对出版年 2026 的出版社完整检索导出；首发日期与卷期年可能不同，该年度首发总量仍未知，不能据此标为核齐。'
            if note not in unit['notes']:
                unit['notes'] += ' ' + note
    # Springer pages verified in the preceding batch: online date precedes issue date.
    for f in json.loads((ROOT/'data/analyses/increment_20261021/publisher_fetches.json').read_text()):
        meta=dict(f.get('citation_metadata',[]));doi=meta.get('citation_doi','').lower()
        if ('IJCV',2026) in enums or f.get('status')!=200 or not doi.startswith('10.1007/s11263-026-'):continue
        candidates=[r for r in groups[('IJCV',2026)] if r['doi'].lower()==doi]
        if len(candidates)!=1 or title_key(candidates[0]['title'])!=title_key(meta.get('citation_title','')):raise ValueError('Springer article identity mismatch')
        stored=candidates[0];published=meta.get('citation_online_date','').replace('/','-')
        if not published or published>CUTOFF:continue
        ref={key:stored[key] for key in ['paper_id','venue','venue_type','year','track','title','authors','doi','paper_url']};ref['source_url']=f['final_url']
        items.append({**ref,'publication_key':publication_identity(ref),'evidence_kind':'individual_official_page','publication_date':published,'date_precision':'day','observed_by_cutoff':True,'publication_status':'online_published','identity_status':'matched','matched_record_ids':[stored['record_id']]})
        next(x for x in manifest if x['venue']=='IJCV' and x['year']==2026)['known_published_count']+=1
    for unit in manifest:
        if unit['reference_count'] is None and unit['known_published_count']:
            unit['notes']+=f" 已逐篇确认 {unit['known_published_count']} 条正式出版记录；只是已确认子集，不表示该年度总数。"
    release=conn.execute("select release_id from data_releases where status='ready' order by created_at desc limit 1").fetchone()[0]
    result={'version':'formal-publication-20261021-v1','frozen_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),'cutoff':CUTOFF,'catalog_release_id':release,'scope':{'sources':30,'conferences':20,'journals':10,'years':[2023,2024,2025,2026],'rule':'以正式论文集、官方已发表文章目录或已核验出版社文章页面为出版证据；包括 Early Access，排除仅录用/日程/预印本、非研究文献。保留既有轨道范围；有明确首次正式上线日期的按首发年列出，卷期年另外保存；其他来源按官方出版目录年份列出，具体日期未知不猜测。Annals 关联补充不扩展为第 31 个主要来源。'},'status_counts':dict(Counter(x['status'] for x in manifest)),'publication_items':len(items),'unmatched_counts':dict(Counter(x['identity_status'] for x in differences)),'excluded_nonresearch':len(excluded),'rows':manifest}
    output.mkdir(parents=True,exist_ok=True);(output/'formal_publication_20261021.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for name,entries in [('publication_items_20261021',items),('publication_differences_20261021',differences),('publication_excluded_20261021',excluded)]:
        (evidence/(name+'.json')).write_text(json.dumps(entries,ensure_ascii=False))
        fields=['publication_key','venue','venue_type','year','volume_year','catalog_year','publisher_year','paper_id','title','authors','doi','paper_url','source_url','track','evidence_kind','publication_date','date_precision','identity_status','matched_record_ids','exclusion_reason']
        target=output/(name+('.csv.gz' if name.startswith('publication_items') else '.csv'))
        handle=gzip.open(target,'wt',encoding='utf-8-sig',newline='',compresslevel=6) if target.suffix=='.gz' else target.open('w',encoding='utf-8-sig',newline='')
        with handle as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
            for e in entries:w.writerow({**e,'matched_record_ids':' | '.join(e.get('matched_record_ids',[]))})
    fields=['venue','venue_type','year','status','local_record_count','reference_count','known_published_count','matched_reference_count','unmatched_reference_count','ambiguous_reference_count','local_only_count','sources','notes']
    with (output/'formal_publication_20261021.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for e in manifest:w.writerow({**e,'sources':' | '.join(e['sources'])})
    print(result['status_counts']);print('Published inventory',len(items),'differences',result['unmatched_counts'],'nonresearch excluded',len(excluded));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',type=Path,default=ROOT/'data/database/catalog.sqlite');p.add_argument('--evidence',type=Path,default=ROOT/'data/analyses/publication_base_20261021');p.add_argument('--output',type=Path,default=ROOT/'data/catalog/reports');a=p.parse_args();run(a.database,a.evidence,a.output)
