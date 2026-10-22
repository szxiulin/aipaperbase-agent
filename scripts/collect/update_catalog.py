"""Stage public metadata, preserve curated rows, and audit incremental updates.

No credentials, model calls, deletions or production database writes. Collection
and enrichment write only inside --run. Integration is a separate validated step.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import unescape
import io
import json
import re
import sys
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from lxml import html

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.collect import build_catalog as base
from scripts.collect import enrich_abstracts as enrich


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Fetcher:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url, suffix='.html'):
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + suffix)
        if path.exists():
            return path.read_bytes()
        for attempt in range(3):
            try:
                r = requests.get(url, headers={'User-Agent': 'PaperCatalogMetadata/1.0'}, timeout=35)
                r.raise_for_status()
                path.write_bytes(r.content)
                path.with_suffix(path.suffix + '.source.json').write_text(json.dumps(
                    {'url': url, 'fetched_at': now(), 'sha256': hashlib.sha256(r.content).hexdigest()}))
                time.sleep(.2)
                return r.content
            except requests.RequestException as exc:
                if getattr(exc.response, 'status_code', None) in (400, 401, 403, 404) or attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=base.FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def normalize(row):
    result = {k: str(row.get(k) or '').strip() for k in base.FIELDS}
    for key in ('title', 'authors'):
        result[key] = unescape(result[key])
    return result


def title_key(value):
    return base.normalize_title(unescape(value))


def set_abstract(row, abstract, url, name, tier='official'):
    text = base.clean_abstract(abstract)
    if text and not row.get('abstract'):
        row.update(abstract=text, abstract_source_name=name, abstract_source_url=url,
                   abstract_source_tier=tier, abstract_fetched_at=now())


def kdd(fetcher):
    url = 'https://kdd2026.kdd.org/papers/'
    source = fetcher.get(url).decode()
    found = {}
    tracks = {'rtp': 'Research', 'ads': 'Applied Data Science', 'dtb': 'Datasets and Benchmarks',
              'ais': 'AI for Sciences', 'bsi': 'Blue Sky Ideas'}
    for match in re.finditer(r'const\s+(cycle\dPapers)\s*=\s*(\[)', source):
        items, _ = json.JSONDecoder().raw_decode(source[match.start(2):])
        for item in items:
            if item['track'] not in tracks:
                raise ValueError('Unreviewed KDD track: ' + item['track'])
            doi = item['url'].removeprefix('https://doi.org/').lower()
            authors = '; '.join(strip_affiliations(x).strip() for x in item['authors'].split(';'))
            row = asdict(base.Paper(paper_id=doi, venue='KDD', venue_type='conference', year=2026,
                track=tracks[item['track']], title=item['title'], authors=authors, doi=doi,
                paper_url=item['url'], source_name='KDD official published paper list', source_url=url,
                list_status='final', verification_status='verified_official'))
            if doi in found and base.normalize_title(found[doi]['title']) != title_key(row['title']):
                raise ValueError('KDD DOI title conflict: ' + doi)
            found.setdefault(doi, row)
    if not found:
        raise ValueError('KDD list empty')
    return list(found.values())


def three_dv(fetcher):
    url = 'https://3dvconf.github.io/2026/schedule.csv'
    items = list(csv.DictReader(io.StringIO(fetcher.get(url, '.csv').decode('utf-8-sig'))))
    rows = []
    for item in items:
        if item['Decision'] not in ('Accept (Oral)', 'Accept (Poster)'):
            continue
        row = asdict(base.Paper(paper_id=item['ID'], venue='3DV', venue_type='conference', year=2026,
            track='main', title=item['Title'], authors=item['Authors'].replace(', ', '; '),
            paper_url=item['PDF Link'], pdf_url='', source_name='3DV official accepted paper schedule',
            source_url=url, list_status='final'))
        if '/forum?id=' in row['paper_url']:
            row['pdf_url'] = row['paper_url'].replace('/forum?id=', '/pdf?id=')
        set_abstract(row, item['Abstract'], url, '3DV official accepted paper schedule')
        rows.append(row)
    return rows



def strip_affiliations(value):
    """Remove balanced affiliation parentheses, including nested institution names."""
    depth = 0
    result = []
    for char in value.translate(str.maketrans('（）', '()')):
        if char == '(':
            if not depth:
                while result and result[-1].isspace():
                    result.pop()
            depth += 1
        elif char == ')':
            if not depth:
                raise ValueError('Unbalanced affiliation parentheses')
            depth -= 1
        elif not depth:
            result.append(char)
    if depth:
        raise ValueError('Unbalanced affiliation parentheses')
    return ''.join(result)


def official_page_authors(content, expected_title):
    document = html.fromstring(content, parser=html.HTMLParser(encoding='utf-8'))
    title = ' '.join(document.xpath('//h1[contains(@class,"event-title")]//text()')).strip()
    authors = ' '.join(document.xpath('//div[contains(@class,"event-organizers")]//text()')).strip()
    if title_key(title) != title_key(expected_title) or not authors:
        raise ValueError('Official author page identity mismatch or missing authors')
    return '; '.join(part.strip() for part in authors.split('⋅'))


def download_rows(venue, host, items, author_fallback=None):
    rows = []
    for item in items:
        if item.get('type') not in ('Poster', 'Oral', 'Spotlight'):
            continue
        url = item.get('virtualsite_url', '')
        if not url or not item.get('name'):
            raise ValueError('Missing official event identity')
        if not re.search(r'/2026/(?:poster|oral|spotlight)/[^/]+$', url, re.I):
            continue  # No paper-level identity: retain only in source snapshot for review.
        # Virtual event IDs occupy a different namespace from submission IDs.
        pid = 'virtual-' + url.rstrip('/').split('/')[-1]
        try:
            authors = strip_affiliations(item.get('speakers/authors', '')).replace(', ', '; ')
        except ValueError:
            if author_fallback is None:
                raise
            authors = author_fallback(item)
        row = asdict(base.Paper(paper_id=pid,venue=venue,venue_type='conference',year=2026,
            track='accepted paper: ' + item['type'].lower(),title=item['name'],authors=authors,
            paper_url=url,source_name=f'{venue} official program download',source_url=f'https://{host}/Downloads/2026',
            list_status='rolling',verification_status='rolling'))
        set_abstract(row,item.get('abstract',''),row['source_url'],row['source_name'])
        rows.append(normalize(row))
    return rows


def downloads(args):
    hosts={'ECCV':'eccv.ecva.net','NeurIPS':'neurips.cc','ICML':'icml.cc','ICLR':'iclr.cc','COLM':'colm.cc'}
    fetcher = Fetcher(args.run / 'cache')
    for venue,host in hosts.items():
        if args.venues and venue not in args.venues:continue
        path=args.run/'probes'/f'{host}.json';path.parent.mkdir(parents=True,exist_ok=True)
        try:
            if not path.exists():
                s=requests.Session();url=f'https://{host}/Downloads/2026'
                r=s.get(url,timeout=35);r.raise_for_status()
                token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',r.text)
                if not token:raise ValueError('No official download form')
                r=s.post(url,data={'csrfmiddlewaretoken':token.group(1),'format':'5','posters':'on','submitaction':'Download Data'},
                         headers={'Referer':url},timeout=(20,180));r.raise_for_status()
                items=r.json();path.write_text(json.dumps(items,ensure_ascii=False))
            items=json.loads(path.read_text());rows=download_rows(venue,host,items,
                author_fallback=lambda item: official_page_authors(fetcher.get(item['virtualsite_url']), item['name']))
            if not rows:raise ValueError('No accepted paper rows')
            dest=args.run/'candidates'/f'{venue}_2026.json';dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                original=json.loads(dest.read_text())
                rows,_=merge_rows(original,rows)
            dest.write_text(json.dumps(rows,ensure_ascii=False))
            print(json.dumps({'venue':venue,'download_rows':len(items),'paper_rows':len(rows)}),flush=True)
        except Exception as exc:print(json.dumps({'venue':venue,'error':str(exc)[:200]}),flush=True)


def merge_rows(old, incoming, approved_titles=None):
    """Match within one venue/year; retain IDs and existing evidence; quarantine conflicts."""
    result = [dict(r) for r in old]
    conflicts, changes = [], []
    approved_titles = approved_titles or {}
    indexes = {key: defaultdict(list) for key in ('paper_id', 'doi', 'title', 'paper_url')}
    def index(row, i):
        for key in indexes:
            value = title_key(row['title']) if key == 'title' else row.get(key, '').casefold()
            provisional_ijcai = row.get('venue') == 'IJCAI' and 'accepted' in row.get('source_name', '').lower()
            if value and not (provisional_ijcai and key in ('paper_id', 'doi', 'paper_url')):
                indexes[key][value].append(i)
    for i, row in enumerate(result):
        index(row, i)
    seen = set()
    for raw in incoming:
        row = normalize(raw)
        title = title_key(row['title'])
        candidates = set()
        for key in ('paper_id', 'doi'):
            value = row[key].casefold()
            if value:
                candidates.update(indexes[key].get(value, []))
        urls = indexes['paper_url'].get(row['paper_url'].casefold(), [])
        if row['paper_url'] and len(urls) == 1:
            candidates.update(urls)
        if not candidates:
            title_matches = indexes['title'].get(title, [])
            # Different registered DOIs can legitimately share a title (e.g. front matter).
            candidates = {i for i in title_matches if not (row['doi'] and result[i]['doi']
                and row['doi'].casefold() != result[i]['doi'].casefold()
                and not (row['venue'] == 'IJCAI' and 'accepted' in result[i]['source_name'].lower()))}
        if len(candidates) > 1:
            conflicts.append({'reason': 'ambiguous_match', 'incoming': row, 'old_ids': [result[i]['paper_id'] for i in sorted(candidates)]})
            continue
        if candidates:
            i = next(iter(candidates)); prev = result[i]
            if title_key(prev['title']) != title:
                approval = approved_titles.get(prev['paper_id'])
                if approval != {'old_title': prev['title'], 'new_title': row['title']}:
                    conflicts.append({'reason': 'identifier_title_conflict', 'incoming': row, 'old': prev})
                    continue
                changes.append({'kind':'reviewed_title_correction','paper_id':prev['paper_id'],**approval})
                prev['title'] = row['title']
            ijcai_promotion = row['venue'] == 'IJCAI' and 'accepted' in prev['source_name'].lower() and row['source_name'] == 'IJCAI Proceedings'
            if prev['doi'] and row['doi'] and prev['doi'].casefold() != row['doi'].casefold() and not ijcai_promotion:
                conflicts.append({'reason': 'title_doi_conflict', 'incoming': row, 'old': prev})
                continue
            changed = []
            if ijcai_promotion:
                changes.append({'kind':'official_identifier_correction','paper_id':prev['paper_id'],
                    'previous_doi':prev['doi'],'official_doi':row['doi'],'title':row['title']})
                for field in ('doi','paper_url','pdf_url','source_name','source_url','source_tier','verification_status','fetched_at','track'):
                    prev[field]=row[field]
                    changed.append(field)
            for field in ('authors', 'doi', 'arxiv_id', 'pdf_url', 'paper_url'):
                if row[field] and not prev[field]:
                    prev[field] = row[field]; changed.append(field)
            if row['abstract'] and not prev['abstract']:
                for field in enrich.ABSTRACT_FIELDS:
                    prev[field] = row[field]
                changed.append('abstract')
            if row['paper_id'] != prev['paper_id']:
                changes.append({'kind': 'source_id_mapping', 'stored_id': prev['paper_id'], 'source_id': row['paper_id'], 'title': row['title']})
            if changed:
                changes.append({'kind': 'enriched', 'paper_id': prev['paper_id'], 'fields': changed})
            seen.add(prev['paper_id'])
            # Newly filled identifiers participate in subsequent conflict checks.
            for key in ('doi', 'paper_id'):
                value = prev[key].casefold()
                if value and i not in indexes[key][value] and not (ijcai_promotion and key == 'paper_id'):
                    indexes[key][value].append(i)
        else:
            if row['paper_id'] in {r['paper_id'] for r in result}:
                row['paper_id'] = 'published-' + row['paper_id']
            i = len(result); result.append(row); index(row, i); seen.add(row['paper_id'])
            changes.append({'kind': 'added', 'paper_id': row['paper_id'], 'title': row['title']})
    unmatched = [r['paper_id'] for r in old if r['paper_id'] not in seen]
    for prev in result:
        if prev['venue'] == 'IJCAI' and 'accepted' in prev['source_name'].lower() and prev['doi']:
            changes.append({'kind': 'unverified_identifier_removed', 'paper_id': prev['paper_id'],
                            'previous_doi': prev['doi'], 'previous_paper_url': prev['paper_url']})
            # Submission IDs are not proceedings IDs. Keep the accepted-list evidence.
            prev['doi'] = ''
            prev['paper_url'] = prev['source_url']
            prev['pdf_url'] = '' 
    # A CSV has one list status. Preserve rolling unless the existing release was final.
    status = old[0]['list_status'] if old else (result[0]['list_status'] if result else 'rolling')
    for row in result:
        row['list_status'] = status
    return result, {'changes': changes, 'conflicts': conflicts, 'not_in_source': unmatched}



def snapshot(args):
    """Freeze only catalog/label/config inputs; never inspect user data or credentials."""
    files = sorted((ROOT / 'data/catalog').rglob('*.csv'))
    files += sorted((ROOT / 'data/labels').glob('*.csv'))
    files += [ROOT / 'config/areas.json', ROOT / 'config/directions.json']
    manifest = []
    for path in files:
        rel = path.relative_to(ROOT)
        target = args.run / 'baseline' / rel
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Baseline differs; choose a new run directory: ' + str(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
        manifest.append({'path': str(rel), 'sha256': digest})
    (args.run / 'baseline_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'baseline_files': len(manifest)}))


def collect(args):
    fetcher = Fetcher(args.run / 'cache')
    jobs = [('KDD', 2026, lambda: kdd(fetcher)), ('3DV', 2026, lambda: three_dv(fetcher)),
            ('JMLR', 2026, lambda: base.scrape_jmlr(fetcher, 2026, 27)),
            ('TMLR', 2026, lambda: base.scrape_tmlr(fetcher).get(2026, [])),
            ('IJCAI', 2026, lambda: base.scrape_ijcai(fetcher, 2026)),
            ('COLM', 2026, lambda: base.scrape_colm(fetcher, 2026)),
            ('ECCV', 2026, lambda: base.scrape_eccv(fetcher, 2026)),
            ('NeurIPS', 2026, lambda: base.scrape_neurips(fetcher, 2026)),
            ('MLSys', 2026, lambda: base.scrape_mlsys(fetcher, 2026)),
            ('CVPR', 2026, lambda: base.scrape_cvf(fetcher, 'CVPR', 2026))]
    for venue in ('ACL', 'EMNLP'):
        jobs.append((venue, 2026, lambda v=venue: base.scrape_acl(fetcher, v, 2026, f'2026.{v.lower()}')))
    for venue in ('ACM MM', 'SIGIR', 'WWW'):
        jobs.append((venue, 2026, lambda v=venue: base.scrape_dblp_conference(fetcher, v, 2026, base.DBLP_CONFERENCES[v])))
    for venue, (issn, official) in base.JOURNALS.items():
        for year in range(2023, 2027):
            jobs.append((venue, year, lambda v=venue,y=year,i=issn,u=official: base.crossref_journal(fetcher,v,y,i,u)))
    if args.venues:
        jobs = [j for j in jobs if j[0] in args.venues]
    def work(job):
        venue, year, fn = job; target = args.run/'candidates'/f'{venue}_{year}.json'
        if target.exists():
            return {'venue': venue, 'year': year, 'state': 'cached', 'rows': len(json.loads(target.read_text()))}
        try:
            rows = [normalize(asdict(r) if isinstance(r, base.Paper) else r) for r in fn()]
            if not rows:
                raise ValueError('No records returned; source completeness not established')
            target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(rows,ensure_ascii=False))
            return {'venue':venue,'year':year,'state':'collected','rows':len(rows)}
        except Exception as exc:
            return {'venue':venue,'year':year,'state':'unresolved','error':f'{type(exc).__name__}: {exc}'}
    status_path = args.run / 'collection_status.json'
    previous_status = json.loads(status_path.read_text()) if status_path.exists() else []
    results = {(x['venue'], x['year']): x for x in previous_status}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done in as_completed([pool.submit(work,j) for j in jobs]):
            result=done.result(); results[(result['venue'], result['year'])] = result
            print(json.dumps(result,ensure_ascii=False),flush=True)
            status_path.write_text(json.dumps(list(results.values()),ensure_ascii=False,indent=2))


def stage(args):
    reports=[]
    baseline=args.run/'baseline/data/catalog'
    candidates=args.run/'candidates'
    paths={}
    for path in baseline.glob('*/*/*.csv'):
        old=read_csv(path)
        if old:
            paths[(old[0]['venue'],old[0]['year'])]=(path.relative_to(baseline),old)
    for source in candidates.glob('*.json'):
        incoming=json.loads(source.read_text())
        if not incoming:continue
        venue,year=incoming[0]['venue'],incoming[0]['year'];key=(venue,year)
        rel,old=paths.get(key,(Path('journals' if incoming[0]['venue_type']=='journal' else 'conferences')/venue/f'{venue.lower()}_{year}.csv',[]))
        approvals_path=args.run/'approved_titles.json'
        approvals=json.loads(approvals_path.read_text()).get(f'{venue}_{year}',{}) if approvals_path.exists() else {}
        merged,report=merge_rows(old,incoming,approvals)
        report.update(venue=venue,year=year,old_rows=len(old),source_rows=len(incoming),merged_rows=len(merged))
        # Source conflicts are quarantined, never applied automatically.
        write_csv(args.run/'catalog'/rel,merged);reports.append(report)
        paths.pop(key,None)
    for rel,old in paths.values():write_csv(args.run/'catalog'/rel,old)
    (args.run/'merge_report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
    print(json.dumps({'added':sum(sum(c['kind']=='added' for c in r['changes']) for r in reports),
                      'conflicts':sum(len(r['conflicts']) for r in reports),'sources':len(reports)}))


def enrich_missing(args):
    fetcher=Fetcher(args.run/'cache');tasks=[];catalogs={}
    for p in (args.run/'catalog').glob('*/*/*.csv'):
        rows=read_csv(p); catalogs[p]=rows
        for r in rows:
            if not r['abstract'] and (not args.venues or r['venue'] in args.venues):tasks.append((p,r))
    if args.limit:tasks=tasks[:args.limit]
    failures=[];counts=Counter()
    def work(item):
        path,row=item;url=row['paper_url'];host=urlparse(url).hostname or ''
        try:
            if host in enrich.OFFICIAL_ABSTRACT_HOSTS or host in ('www.ecva.net','ecva.net','eccv.ecva.net','link.springer.com','link.springernature.com'):
                raw=fetcher.get(url);text=enrich.extract_official_page_abstract(raw,row['title'])
                if text:return path,row,text,url,'Official paper page','official'
            if row['doi']:
                from urllib.parse import quote
                url='https://api.crossref.org/works/'+quote(row['doi'],safe='')
                msg=json.loads(fetcher.get(url,'.json'))['message']
                if base.normalize_title((msg.get('title') or [''])[0])==title_key(row['title']):
                    text=base.clean_abstract(msg.get('abstract'))
                    if text:return path,row,text,url,'Crossref publisher-registered metadata','official_plus_secondary'
            return path,row,'','','',''
        except Exception as exc:return path,row,'',url,type(exc).__name__,str(exc)[:180]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i,done in enumerate(as_completed([pool.submit(work,t) for t in tasks]),1):
            path,row,text,url,name,tier=done.result()
            if text:set_abstract(row,text,url,name,tier);counts[row['venue']]+=1
            elif name:failures.append({'venue':row['venue'],'paper_id':row['paper_id'],'url':url,'error':tier})
            if i%100==0:
                for p,rs in catalogs.items():write_csv(p,rs)
                print(json.dumps({'processed':i,'total':len(tasks),'filled':dict(counts),'failures':len(failures)}),flush=True)
    for p,rs in catalogs.items():write_csv(p,rs)
    report={'attempted':len(tasks),'filled':dict(counts),'failures':failures}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    (args.run/f'enrichment_{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'attempted':len(tasks),'filled':dict(counts),'failures':len(failures)}))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('phase',choices=['snapshot','collect','downloads','stage','enrich'])
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--venues',nargs='+')
    ap.add_argument('--workers',type=int,default=3)
    ap.add_argument('--limit',type=int,default=0)
    args=ap.parse_args();args.run=args.run.resolve();args.run.mkdir(parents=True,exist_ok=True)
    if not 1<=args.workers<=4:ap.error('workers must be between 1 and 4')
    {'snapshot':snapshot,'collect':collect,'downloads':downloads,'stage':stage,'enrich':enrich_missing}[args.phase](args)


if __name__=='__main__':main()
