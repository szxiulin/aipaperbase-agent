"""Backfill abstracts from the free, unauthenticated S2 batch endpoint.

Only exact requested identifier + normalized title matches are accepted. The
caller supplies a staged catalog. Checkpoints and responses remain in --run.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
import sys
import requests
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.collect.update_catalog import read_csv, write_csv, set_abstract, title_key, now

ENDPOINT = 'https://api.semanticscholar.org/graph/v1/paper/batch'
FIELDS = 'title,abstract,externalIds,url,authors'


def normalized_title(title):
    return title_key(re.sub(r'</?(?:i|b|sup|sub|jats:[\w-]+)\b[^>]*>', '', title))


def accepts(requested, row, paper):
    if not paper or not paper.get('abstract'):
        return False
    if normalized_title(row['title']) != normalized_title(paper.get('title', '')):
        return False
    external = paper.get('externalIds') or {}
    if requested.startswith('DOI:'):
        return str(external.get('DOI') or '').casefold() == requested[4:].casefold()
    if requested.startswith('ARXIV:'):
        clean = lambda s: re.sub(r'v\d+$', '', str(s).casefold())
        return clean(external.get('ArXiv')) == clean(requested[6:])
    return requested.startswith('URL:')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument('--kind', choices=['DOI','ARXIV','URL'], default='DOI')
    args=parser.parse_args()
    if not 1 <= args.batch_size <= 100: parser.error('batch-size must be 1..100')
    cache=args.run/'s2_batches';cache.mkdir(parents=True,exist_ok=True)
    catalogs={p:read_csv(p) for p in (args.run/'catalog').glob('*/*/*.csv')}
    tasks=defaultdict(list)
    for path,rows in catalogs.items():
        for row in rows:
            if row['abstract']: continue
            key=('DOI:'+row['doi'] if row['doi'] else 'ARXIV:'+row['arxiv_id'] if row['arxiv_id'] else
                 'URL:'+row['paper_url'] if 'openreview.net/forum?id=' in row['paper_url'] else '')
            if key.startswith(args.kind+':'): tasks[key].append((path,row))
    keys=sorted(tasks);counts=Counter();rejected=[];done=0;errors=[]
    for start in range(0,len(keys),args.batch_size):
        batch=keys[start:start+args.batch_size]
        key=hashlib.sha256(json.dumps(batch).encode()).hexdigest()
        target=cache/(key+'.json')
        try:
            if target.exists(): papers=json.loads(target.read_text())['papers']
            else:
                for attempt in range(4):
                    response=requests.post(ENDPOINT,params={'fields':FIELDS},json={'ids':batch},timeout=60)
                    if response.status_code!=429: break
                    if attempt==3: response.raise_for_status()
                    delay=min(30*(2**attempt),120)
                    print(json.dumps({'rate_limited':True,'retry_after_seconds':delay}),flush=True)
                    time.sleep(delay)
                response.raise_for_status();papers=response.json()
                if not isinstance(papers,list) or len(papers)!=len(batch): raise ValueError('Unexpected batch shape')
                target.write_text(json.dumps({'requested_ids':batch,'fetched_at':now(),'papers':papers},ensure_ascii=False))
                time.sleep(5)
            for requested,paper in zip(batch,papers):
                for path,row in tasks[requested]:
                    if accepts(requested,row,paper):
                        set_abstract(row,paper['abstract'],paper['url'],'Semantic Scholar exact identifier and title','third_party_crosschecked')
                        counts[row['venue']]+=1
                    else: rejected.append({'requested_id':requested,'venue':row['venue'],'paper_id':row['paper_id'],'reason':'missing abstract or identifier/title mismatch'})
            done+=len(batch)
            for p,rows in catalogs.items(): write_csv(p,rows)
            print(json.dumps({'processed_identifiers':done,'total':len(keys),'filled':dict(counts)}),flush=True)
        except (requests.RequestException,ValueError) as exc:
            errors.append({'batch_start':start,'error':str(exc)[:250]})
            break  # Keep checkpoints; do not hammer a restricted source.
    report={'kind':args.kind,'identifiers':len(keys),'processed_identifiers':done,'filled':dict(counts),'rejected':rejected,'errors':errors}
    stamp=now().replace(':','').replace('+','_')
    (args.run/f'free_abstracts_{args.kind}_{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='rejected'}),flush=True)


if __name__=='__main__':main()
