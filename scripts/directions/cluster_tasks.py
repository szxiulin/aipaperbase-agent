"""Reproducible, area-scoped draft clustering. Never activates product taxonomy."""
import os
os.environ.update({k:'4' for k in ['OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS']})
import csv,hashlib,json,time
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import adjusted_rand_score
ROOT=Path(__file__).resolve().parent;J=ROOT/'tasks_v1';D=J/'draft';D.mkdir(exist_ok=True)
def read(p):return [json.loads(l) for l in p.read_text().splitlines()]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def norm(v):return v/np.maximum(np.linalg.norm(v,axis=-1,keepdims=True),1e-12)
def fit(x,w,k,seed=42):
 k=min(k,len(x));km=MiniBatchKMeans(n_clusters=k,random_state=seed,n_init=3,batch_size=1024,max_iter=100,reassignment_ratio=0).fit(x,sample_weight=w)
 c=norm(km.cluster_centers_)
 for _ in range(4):
  a=np.argmax(x@c.T,axis=1);s=np.zeros_like(c);np.add.at(s,a,x*w[:,None]);good=np.linalg.norm(s,axis=1)>0;c[good]=norm(s[good])
 return c,np.argmax(x@c.T,axis=1)
def main():
 start=time.time();src=json.loads((J/'source_manifest.json').read_text());comp=json.loads((J/'embeddings/completion.json').read_text())
 assert sha(J/'task_vocabulary.jsonl')==src['vocabulary_sha256'];assert sha(J/'paper_tasks.jsonl')==src['paper_tasks_sha256'];assert comp['model_revision']==src['model_revision']
 assert sha(J/'embeddings/tasks.1024.f16.npy')==comp['files']['1024'];assert sha(ROOT/'outputs/merged/papers.1024.f16.npy')==src['paper_vector_sha256']
 vocab=read(J/'task_vocabulary.jsonl');vix={r['task']:i for i,r in enumerate(vocab)};tv=np.load(J/'embeddings/tasks.1024.f16.npy',mmap_mode='r');pv=np.load(ROOT/'outputs/merged/papers.1024.f16.npy',mmap_mode='r')
 ids=read(ROOT/'outputs/merged/ids.jsonl');print('ID_EXAMPLE',ids[0],flush=True)
 ids=[r['entity_id'] if isinstance(r,dict) else r for r in ids];pix={v:i for i,v in enumerate(ids)};papers={r['entity_id']:r for r in read(ROOT/'papers.jsonl')};rows=read(J/'paper_tasks.jsonl');assert len(rows)==len(pix)==121742 and set(pix)=={r['entity_id'] for r in rows}==set(papers)
 areas=json.loads((J/'areas.json').read_text());areas=areas['areas'] if isinstance(areas,dict) else areas;anames={r['id']:r.get('zh',r['id']) for r in areas}
 grouped=defaultdict(list)
 for r in rows:grouped[r['area']].append(r)
 clusters=[];assignments=[];metrics={};allcenters=[];centerids=[]
 for area,rr in sorted(grouped.items()):
  groups=defaultdict(list)
  for r in rr:groups[r['task']].append(r)
  tasks=sorted(groups);weights=np.sqrt(np.array([len(groups[t]) for t in tasks],dtype=np.float32))
  context=np.stack([norm(np.asarray(pv[[pix[r['entity_id']] for r in groups[t]]],dtype=np.float32).mean(axis=0)) for t in tasks]);x=norm(.75*np.asarray(tv[[vix[t] for t in tasks]],dtype=np.float32)+.25*context)
  k=max(3,min(24,round(np.sqrt(len(rr))/6)))
  if area=='cv_lowlevel':k=20
  if area=='other':k=3
  microk=min(len(tasks),max(k*3,k));mc,ma=fit(x,weights,microk);mw=np.bincount(ma,weights=weights,minlength=microk).astype(np.float32)
  active=mw>0;mc=mc[active];mw=mw[active];c,ca=fit(mc,mw,k);c2,ca2=fit(mc,mw,k,43)
  score=x@c.T;na=score.argmax(axis=1)
  # Final direction centers refine on all task nodes, maintaining cosine geometry.
  for _ in range(3):
   sums=np.zeros_like(c);np.add.at(sums,na,x*weights[:,None]);good=np.linalg.norm(sums,axis=1)>0;c[good]=norm(sums[good]);score=x@c.T;na=score.argmax(axis=1)
  metrics[area]={'papers':len(rr),'task_nodes':len(tasks),'requested_directions':k,'microclusters':len(mc),'micro_partition_stability_ari':float(adjusted_rand_score(ca,ca2))}
  # Stable display order by representative task, rather than arbitrary KMeans labels.
  order=sorted(set(na.tolist()),key=lambda z:tasks[int(np.argmax(np.where(na==z,score[:,z],-2)))])
  mapping={z:f'{area}-d{i+1:02}' for i,z in enumerate(order)}
  for z in order:
   ni=np.where(na==z)[0];taskfreq=sorted([(tasks[i],len(groups[tasks[i]])) for i in ni],key=lambda p:(-p[1],p[0]));members=[]
   for i in ni:
    for r in groups[tasks[i]]:
     feat=norm(.75*np.asarray(tv[vix[r['task']]],dtype=np.float32)+.25*np.asarray(pv[pix[r['entity_id']]],dtype=np.float32));ss=feat@c.T;best=float(ss[z]);other=float(np.max(np.delete(ss,z))) if len(c)>1 else -1;flag=best<.55 or best-other<.02
     # Phrase nodes stay together; per-paper score only signals contextual ambiguity.
     assignments.append({'entity_id':r['entity_id'],'area':area,'task':r['task'],'draft_direction':mapping[z],'cosine':round(best,6),'margin':round(best-other,6),'needs_review':int(flag),'area_confidence':r['area_confidence']})
     p=papers[r['entity_id']];members.append({'entity_id':r['entity_id'],'task':r['task'],'title':p['title'],'abstract':p.get('abstract') or '', 'cosine':best,'margin':best-other,'needs_review':flag})
   reps=[];seen=set()
   for m in sorted(members,key=lambda m:-m['cosine']):
    if m['task'] in seen:continue
    reps.append(m);seen.add(m['task'])
    if len(reps)==4:break
   boundary=sorted(members,key=lambda m:m['margin'])[:3]
   nearest=np.argsort(-(c@c[z]))
   item={'id':mapping[z],'area':area,'area_name':anames[area],'suggested_name_en':reps[0]['task'],'paper_count':len(members),'task_count':len(ni),'top_tasks':[{'task':t,'count':n} for t,n in taskfreq[:15]],'representatives':reps,'boundary_papers':boundary,'mean_cosine':float(np.mean([m['cosine'] for m in members])),'needs_review_count':sum(m['needs_review'] for m in members),'title_only_count':sum(not m['abstract'] for m in members),'nearest_directions':[{'id':mapping[int(j)],'cosine':float(c[z]@c[j])} for j in nearest if int(j)!=z and int(j) in mapping][:2],'status':'pending_review'}
   clusters.append(item);allcenters.append(c[z]);centerids.append(mapping[z])
  print('AREA_COMPLETE',area,len(rr),len(order),flush=True)
 assert len(assignments)==121742==len({r['entity_id'] for r in assignments});assert sum(c['paper_count'] for c in clusters)==121742
 with (D/'paper_direction_draft.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(assignments[0]));w.writeheader();w.writerows(assignments)
 np.save(D/'direction_centers.npy',np.stack(allcenters));(D/'direction_center_ids.json').write_text(json.dumps(centerids))
 (D/'clusters.json').write_text(json.dumps(clusters,ensure_ascii=False,indent=2))
 report={'phase':'draft_only','rows':len(assignments),'unique_task_phrases':len(vocab),'area_task_nodes':sum(m['task_nodes'] for m in metrics.values()),'directions':len(clusters),'needs_review_papers':sum(r['needs_review'] for r in assignments),'source':src,'task_embeddings':comp,'areas':metrics,'seconds':time.time()-start,'method':{'feature':'normalize(0.75 * task unit vector + 0.25 * mean paper-context unit vector)','weight':'sqrt(paper count per area-task node)','algorithm':'seed42 MiniBatchKMeans microclusters, weighted coarse grouping, cosine refinement','singletons':'all participate in fit','thresholds':'cosine <0.55 or same-phrase assigned-center margin <0.02; heuristic flags, not truth','note':'primary areas frozen; node assignments provisional; no formal taxonomy written'}}
 (D/'clustering_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('CLUSTERING_COMPLETE',len(clusters),flush=True)
if __name__=='__main__':main()
