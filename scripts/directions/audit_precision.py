"""Independent precision and vector-dimension checks on fixed task rows."""
import os
os.environ.update({'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','TOKENIZERS_PARALLELISM':'false','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
import json,subprocess
from pathlib import Path
import numpy as np
import torch
from transformers import AutoModel,AutoTokenizer
R=Path(__file__).resolve().parent;J=R/'tasks_v1';O=J/'embeddings'
def main():
 gpu=os.environ['CUDA_VISIBLE_DEVICES'];out=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader,nounits'],text=True);assert not any(l.split(',')[0].strip()==gpu for l in out.splitlines())
 torch.set_num_threads(2);torch.set_num_interop_threads(1);rows=[json.loads(l) for l in (J/'task_vocabulary.jsonl').read_text().splitlines()];ix=[0,32,900,3600,4000,15000,33024,33025,33120,40000,50000,60000,70000,80000,82229];texts=[rows[i]['task'] for i in ix]
 tok=AutoTokenizer.from_pretrained(R/'models/Qwen3-Embedding-8B',padding_side='left',local_files_only=True);m=AutoModel.from_pretrained(R/'models/Qwen3-Embedding-8B',torch_dtype=torch.float16,attn_implementation='sdpa',low_cpu_mem_usage=True,local_files_only=True).eval().to('cuda:0');batch=tok(texts,padding=True,truncation=True,max_length=256,return_tensors='pt').to('cuda:0')
 with torch.inference_mode():fp=m(**batch,use_cache=False).last_hidden_state[:,-1,:].float().cpu().numpy()
 m.to(dtype=torch.bfloat16)
 with torch.inference_mode():bf=m(**batch,use_cache=False).last_hidden_state[:,-1,:].float().cpu().numpy()
 assert np.isfinite(bf).all();report={'sample_rows':ix,'texts':texts,'dimensions':{},'fp16_finite_rows':int(np.isfinite(fp).all(axis=1).sum()),'bf16_finite_rows':len(ix)}
 for d in [1024,4096]:
  raw=bf[:,:d];b=raw/np.linalg.norm(raw,axis=1,keepdims=True);stored=np.asarray(np.load(O/f'tasks.{d}.f16.npy',mmap_mode='r')[ix],dtype=np.float32);valid=np.isfinite(fp).all(axis=1);f=fp[valid,:d];f=f/np.linalg.norm(f,axis=1,keepdims=True)
  cos=np.sum(b*stored,axis=1)/np.linalg.norm(stored,axis=1);precision=np.sum(f*b[valid],axis=1)
  report['dimensions'][str(d)]={'stored_vs_bf16_cosines':cos.tolist(),'minimum_stored_vs_bf16':float(cos.min()),'minimum_fp16_vs_bf16':float(precision.min())}
  assert cos.min()>.995 and precision.min()>.995
 a=np.asarray(np.load(O/'tasks.1024.f16.npy',mmap_mode='r')[ix],dtype=np.float32);b=np.asarray(np.load(O/'tasks.4096.f16.npy',mmap_mode='r')[ix,:1024],dtype=np.float32);b/=np.linalg.norm(b,axis=1,keepdims=True);assert np.max(np.abs(a-b))<.001
 report['dimension_prefix_normalization_verified']=True;report['passed']=True;(O/'precision_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
