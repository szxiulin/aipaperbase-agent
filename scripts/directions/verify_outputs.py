"""Independent delivery checks, including the normalization aliasing regression."""
import argparse,csv,hashlib,json
from collections import Counter
from pathlib import Path
import numpy as np

def verify(inputs,vectors,draft):
 checks=[]
 def check(name,ok):checks.append({'name':name,'passed':bool(ok)})
 def js(p):return json.loads(p.read_text())
 def lines(p):return [json.loads(l) for l in p.read_text().splitlines()]
 def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
 src=js(inputs/'source_manifest.json');v=lines(inputs/'task_vocabulary.jsonl');papers=lines(inputs/'paper_tasks.jsonl');comp=js(vectors/'completion.json');cc=js(draft/'named_clusters.json');aa=list(csv.DictReader((draft/'paper_direction_draft.csv').open()))
 check('vocabulary hash and unique IDs',sha(inputs/'task_vocabulary.jsonl')==src['vocabulary_sha256'] and len(v)==src['unique_tasks']==len({r['task_id'] for r in v}))
 check('source paper hash and exact unique IDs',sha(inputs/'paper_tasks.jsonl')==src['paper_tasks_sha256'] and len(papers)==src['rows']==len({r['entity_id'] for r in papers}))
 check('task frequency and singleton preservation',sum(r['paper_count'] for r in v)==src['rows'] and sum(r['paper_count']==1 for r in v)==src['singleton_tasks'])
 arrays={d:np.load(vectors/f'tasks.{d}.f16.npy',mmap_mode='r') for d in [1024,4096]}
 for d,a in arrays.items():
  check(f'{d} shape dtype hash',a.shape==(len(v),d) and a.dtype==np.float16 and sha(vectors/f'tasks.{d}.f16.npy')==comp['files'][str(d)])
  finite=True;maxerr=0
  for i in range(0,len(v),1024):
   x=np.asarray(a[i:i+1024],dtype=np.float32);finite&=np.isfinite(x).all();maxerr=max(maxerr,float(np.max(np.abs(np.linalg.norm(x,axis=1)-1))))
  check(f'{d} full finite unit-norm scan',finite and maxerr<.002)
 delta=0
 for i in range(0,len(v),1024):
  a=np.asarray(arrays[1024][i:i+1024],dtype=np.float32);b=np.asarray(arrays[4096][i:i+1024,:1024],dtype=np.float32);b/=np.linalg.norm(b,axis=1,keepdims=True);delta=max(delta,float(np.max(np.abs(a-b))))
 check('all-row 1024/4096 independent prefix normalization regression',delta<.001)
 source={r['entity_id']:r for r in papers};counts=Counter(r['draft_direction'] for r in aa);clusters={r['id']:r for r in cc}
 check('assignment coverage exactly matches source unique IDs',len(aa)==src['rows']==len({r['entity_id'] for r in aa}) and {r['entity_id'] for r in aa}==set(source))
 check('assignment area task range preserved',all(r['area']==source[r['entity_id']]['area'] and r['task']==source[r['entity_id']]['task'] and r['draft_direction'] in clusters and clusters[r['draft_direction']]['area']==r['area'] for r in aa))
 check('direction membership counts reconcile',set(counts)==set(clusters) and all(counts[c['id']]==c['paper_count'] for c in cc) and sum(c['paper_count'] for c in cc)==src['rows'])
 check('all area-task nodes including singleton nodes retained',{(r['area'],r['task']) for r in aa}=={(r['area'],r['task']) for r in papers})
 assigned={r['entity_id']:r['draft_direction'] for r in aa}
 check('representatives and boundary papers belong to direction',all(assigned.get(p['entity_id'])==c['id'] for c in cc for p in c['representatives']+c['boundary_papers']))
 check('all draft names definitions and pending statuses present',all(c.get('name_zh') and c.get('name_en') and c.get('definition') and c.get('boundary_note') and c['status']=='pending_review' for c in cc))
 audit=js(vectors/'precision_audit.json');check('independent mixed precision audit',audit['passed'] and audit['dimension_prefix_normalization_verified'])
 report={'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'skipped':0,'checks':checks,'maximum_prefix_component_delta':delta,'rows':len(aa),'tasks':len(v),'directions':len(cc)}
 (draft/'independent_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--vectors',type=Path,required=True);p.add_argument('--draft',type=Path,required=True);a=p.parse_args();r=verify(a.inputs,a.vectors,a.draft);print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(bool(r['failed']))
