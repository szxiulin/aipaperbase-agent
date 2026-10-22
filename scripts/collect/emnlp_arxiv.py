"""Free arXiv enrichment gated by official title and presenter identity."""
import argparse
import csv
from datetime import date
import io
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from lxml import etree
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.collect.update_catalog import Fetcher,title_key
NS={'a':'http://www.w3.org/2005/Atom'}


def query_url(rows):
    query=' OR '.join('ti:"'+row['Title'].replace('"','')+'"' for row in rows)
    return 'https://export.arxiv.org/api/query?'+urlencode({'search_query':query,'max_results':100})


def next_batch(rows, start):
    batch=rows[start:start+20]
    while len(batch)>1 and len(query_url(batch))>1900:
        batch=batch[:-1]
    return batch


def name_key(value):
    return sorted(re.findall(r'[^\W\d_]+',value.casefold()))


def match(row, entries):
    candidates=[]
    for entry in entries:
        title=entry.findtext('a:title',default='',namespaces=NS)
        authors=[x.text for x in entry.findall('a:author/a:name',NS)]
        presenter=name_key(row['Presenters Name'])
        if title_key(title)!=title_key(row['Title']) or not presenter or not any(name_key(a)==presenter for a in authors):
            continue
        url=entry.findtext('a:id',default='',namespaces=NS)
        abstract=entry.findtext('a:summary',default='',namespaces=NS)
        published=entry.findtext('a:published',default='',namespaces=NS)
        if url and abstract and re.fullmatch(r'\d{4}-\d{2}-\d{2}',published[:10]) and published[:10]<=date.today().isoformat():
            candidates.append({'paper_id':'program-'+row['Paper ID #-track)'],'title':row['Title'],
                'authors':authors,'url':url.replace('http:','https:'),'abstract':abstract,
                'presenter':row['Presenters Name'],'published':published})
    return candidates[0] if len(candidates)==1 else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();run=args.run;fetcher=Fetcher(run/'cache')
    rows=[x for x in csv.DictReader(io.StringIO((run/'emnlp.raw').read_text())) if x['Paper ID #-track)'].endswith('-MAIN')]
    output=run/'emnlp_arxiv_results.json';progress=run/'arxiv_progress.json'
    results=json.loads(output.read_text()) if output.exists() else []
    start=json.loads(progress.read_text())['processed'] if progress.exists() else 0
    while start<len(rows):
        batch=next_batch(rows,start)
        try:
            document=etree.fromstring(fetcher.get(query_url(batch),'.xml'))
            entries=document.findall('a:entry',NS)
            for row in batch:
                result=match(row,entries)
                if result:results.append(result)
            start+=len(batch)
            output.write_text(json.dumps(results,ensure_ascii=False))
            progress.write_text(json.dumps({'processed':start,'total':len(rows),'matched':len(results)}))
            print('processed',start,'matched',len(results),flush=True)
        except Exception as exc:
            (run/'arxiv_error.json').write_text(json.dumps({'batch_start':start,'error':str(exc)[:200]}))
            print('STOP',str(exc)[:200],flush=True)
            break
        time.sleep(3)


if __name__=='__main__':main()
