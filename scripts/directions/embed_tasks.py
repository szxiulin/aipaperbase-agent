"""Encode the complete unique task vocabulary locally on one explicitly selected GPU."""
import os
os.environ.update({'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','TOKENIZERS_PARALLELISM':'false','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_IMPLICIT_TOKEN':'1'})
import fcntl,hashlib,json,subprocess,time
from pathlib import Path
import numpy as np
import torch
from transformers import AutoModel,AutoTokenizer
ROOT=Path(__file__).resolve().parent;JOB=ROOT/'tasks_v1';OUT=JOB/'embeddings';OUT.mkdir(exist_ok=True)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def save(p,d):
 t=p.with_suffix(p.suffix+'.tmp')
 with t.open('w') as f:json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 t.replace(p)
def main():
 lock=open(OUT/'job.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 source=json.loads((JOB/'source_manifest.json').read_text());modelinfo=json.loads((ROOT/'model_revision.json').read_text());assert source['model_revision']==modelinfo['revision']
 assert sha(JOB/'task_vocabulary.jsonl')==source['vocabulary_sha256']
 rows=[json.loads(l) for l in (JOB/'task_vocabulary.jsonl').read_text().splitlines()];assert len(rows)==source['unique_tasks']==len({r['task_id'] for r in rows})
 statefile=OUT/'state.json';s=json.loads(statefile.read_text()) if statefile.exists() else {'completed':0,'vocabulary_sha256':source['vocabulary_sha256'],'model_revision':modelinfo['revision'],'compute_seconds':0,'tokens':0,'oom_retries':0,'truncated':0}
 assert s['vocabulary_sha256']==source['vocabulary_sha256'] and s['model_revision']==modelinfo['revision']
 if (OUT/'completion.json').exists():
  c=json.loads((OUT/'completion.json').read_text());assert s['completed']==len(rows)
  for dim in [1024,4096]:assert sha(OUT/f'tasks.{dim}.f16.npy')==c['files'][str(dim)]
  print('TASK_EMBEDDINGS_ALREADY_COMPLETE');return
 gpu=os.environ['CUDA_VISIBLE_DEVICES'];assert gpu.startswith('GPU-') and ',' not in gpu
 def processes():
  out=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader,nounits'],text=True)
  return {l.split(',')[1].strip() for l in out.splitlines() if l.split(',')[0].strip()==gpu}
 assert not processes(),'Selected GPU busy; do not load model'
 torch.set_num_threads(2);torch.set_num_interop_threads(1)
 tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/Qwen3-Embedding-8B',padding_side='left',local_files_only=True)
 model=AutoModel.from_pretrained(ROOT/'models/Qwen3-Embedding-8B',torch_dtype=torch.float16,attn_implementation='sdpa',low_cpu_mem_usage=True,local_files_only=True).eval().to('cuda:0');torch.cuda.synchronize()
 ps=processes();assert len(ps)==1;own=next(iter(ps));print('MODEL_READY',gpu,'NVML_PID',own,flush=True)
 s.setdefault('bf16_prefix_rows',s['completed']);s.setdefault('bf16_fallback_batches',0)
 s.update({'gpu_uuid':gpu,'nvml_pid':own,'pid':os.getpid(),'total':len(rows),'phase':'running','started_at':s.get('started_at',time.time())})
 arrays={}
 for dim in [1024,4096]:
  p=OUT/f'tasks.{dim}.f16.npy'
  if s['completed']:assert p.exists()
  arrays[dim]=np.lib.format.open_memmap(p,mode='r+' if p.exists() else 'w+',dtype=np.float16,shape=(len(rows),dim))
 tasks=[r['task'] for r in rows];lengths=[len(tokenizer.encode(t)) for t in tasks];maxbatch=128;last_check=0
 while s['completed']<len(rows):
  if time.monotonic()-last_check>30:
   assert processes()=={own},'Another process appeared on selected GPU; preserve checkpoint and exit';last_check=time.monotonic()
  begin=s['completed'];end=begin
  while end<min(begin+maxbatch,len(rows)):
   if end>begin and max(lengths[begin:end+1])*(end-begin+1)>2048:break
   end+=1
  bt=time.monotonic();batch=tokenizer(tasks[begin:end],padding=True,truncation=True,max_length=256,return_tensors='pt').to('cuda:0')
  try:
   with torch.inference_mode():vec=model(**batch,use_cache=False).last_hidden_state[:,-1,:].float().cpu().numpy()
   if not np.isfinite(vec).all():
    print('BF16_NUMERIC_FALLBACK',begin,end,flush=True);model.to(dtype=torch.bfloat16)
    with torch.inference_mode():vec=model(**batch,use_cache=False).last_hidden_state[:,-1,:].float().cpu().numpy()
    model.to(dtype=torch.float16);s['bf16_fallback_batches']+=1
   assert np.isfinite(vec).all(),'Non-finite output after stable-precision retry'
  except torch.cuda.OutOfMemoryError:
   s['oom_retries']+=1;torch.cuda.empty_cache();save(statefile,s)
   if maxbatch==1:raise
   maxbatch=max(1,(end-begin)//2);continue
  for dim,a in arrays.items():
   v=vec[:,:dim];v=v/np.linalg.norm(v,axis=1,keepdims=True);assert np.isfinite(v).all();a[begin:end]=v.astype(np.float16);a.flush()
  s['completed']=end;s['compute_seconds']+=time.monotonic()-bt;s['tokens']+=sum(min(n,256) for n in lengths[begin:end]);s['truncated']+=sum(n>256 for n in lengths[begin:end]);s['updated_at']=time.time();s['peak_allocated_gib']=float(torch.cuda.max_memory_allocated()/2**30);save(statefile,s)
  if end%5000<end-begin or end==len(rows):print('PROGRESS',json.dumps(s),flush=True)
 for dim,a in arrays.items():
  for i in range(0,len(rows),1000):
   v=np.asarray(a[i:i+1000],dtype=np.float32);assert np.isfinite(v).all() and np.max(np.abs(np.linalg.norm(v,axis=1)-1))<.002
 a=np.asarray(arrays[1024],dtype=np.float32);index={r['task']:i for i,r in enumerate(rows)}
 probes=['image super-resolution','image deraining','image dehazing','image denoising','semantic segmentation','object detection','retrieval augmented generation','retrieval-augmented generation','llm jailbreak attack','molecular property prediction','machine unlearning','neural architecture search','time series forecasting','sequential recommendation','offline reinforcement learning','novel view synthesis']
 neighbors=[]
 for q in probes:
  if q not in index:continue
  i=index[q];scores=a@a[i];top=np.argsort(-scores)[:9];neighbors.append({'task':q,'neighbors':[{'task':rows[j]['task'],'count':rows[j]['paper_count'],'cosine':float(scores[j])} for j in top if j!=i][:8]})
 save(OUT/'neighbors.json',neighbors)
 s['phase']='finished';s['finished_at']=time.time();save(statefile,s)
 save(OUT/'completion.json',{'rows':len(rows),'unique_ids':len({r['task_id'] for r in rows}),'vocabulary_sha256':source['vocabulary_sha256'],'model':modelinfo['model'],'model_revision':modelinfo['revision'],'inference_dtype':'FP16 with BF16 fallback for non-finite batches; initial BF16 prefix recorded in state','encoding':'raw normalized task; left padding; last token; truncate dimension then L2 normalize; FP16','max_length':256,'dimensions':[1024,4096],'state':s,'files':{str(d):sha(OUT/f'tasks.{d}.f16.npy') for d in arrays}});print('TASK_EMBEDDINGS_COMPLETE',flush=True)
if __name__=='__main__':main()
