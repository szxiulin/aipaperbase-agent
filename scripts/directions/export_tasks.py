"""Export a frozen vocabulary without losing singleton tasks or technical qualifiers."""
import argparse,csv,hashlib,json,re,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
def normalize_task(s):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',s).casefold().strip())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def export(labels,out,index):
 out.mkdir(parents=True,exist_ok=False)
 rows=list(csv.DictReader(labels.open()));assert len(rows)==len({r['entity_id'] for r in rows})
 counts=Counter();by_area=defaultdict(Counter);exported=[]
 for r in rows:
  t=normalize_task(r['task'])
  if not t:raise ValueError('Empty task: '+r['entity_id'])
  counts[t]+=1;by_area[t][r['area']]+=1
  exported.append({k:(t if k=='task' else r[k]) for k in ['entity_id','task','area','area_confidence','method_family','application']})
 vocab=[{'task_id':'task_'+hashlib.sha256(t.encode()).hexdigest()[:20],'task':t,'paper_count':counts[t],'areas':dict(by_area[t])} for t in sorted(counts)]
 for name,rr in [('task_vocabulary.jsonl',vocab),('paper_tasks.jsonl',exported)]:
  with (out/name).open('w') as f:
   for r in rr:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 manifest={'rows':len(rows),'unique_tasks':len(vocab),'singleton_tasks':sum(n==1 for n in counts.values()),'empty_task_rows':0,'labels_sha256':sha(labels),'vocabulary_sha256':sha(out/'task_vocabulary.jsonl'),'paper_tasks_sha256':sha(out/'paper_tasks.jsonl'),'model_revision':'1d8ad4ca9b3dd8059ad90a75d4983776a23d44af','paper_vector_sha256':sha(index/'papers.1024.f16.npy'),'normalization':'NFKC, casefold, strip, collapse whitespace only; preserve punctuation and technical qualifiers','task_prompt':'raw normalized task phrase, no retrieval query instruction'}
 (out/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));return manifest
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--labels',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--index',type=Path,required=True);a=p.parse_args();print(json.dumps(export(a.labels,a.out,a.index),ensure_ascii=False))
