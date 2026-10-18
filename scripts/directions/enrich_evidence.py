"""Choose inspectable representatives without changing clusters or paper membership."""
import argparse,csv,json
from collections import defaultdict
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('draft',type=Path);p.add_argument('--papers',type=Path,required=True);a=p.parse_args();cs=json.loads((a.draft/'clusters.json').read_text());groups=defaultdict(list)
 for r in csv.DictReader((a.draft/'paper_direction_draft.csv').open()):groups[r['draft_direction']].append(r)
 papers={}
 for line in a.papers.open():
  r=json.loads(line);papers[r['entity_id']]={'title':r['title'],'abstract':r.get('abstract') or ''}
 for c in cs:
  candidates=sorted(groups[c['id']],key=lambda r:(-float(r['cosine']),r['entity_id']));chosen=[];seen=set()
  for r in candidates:
   if not papers[r['entity_id']]['abstract'] or r['task'] in seen:continue
   chosen.append(r);seen.add(r['task'])
   if len(chosen)==3:break
  for r in candidates:
   if r['task'] in seen:continue
   chosen.append(r);seen.add(r['task'])
   if len(chosen)>=4:break
  c['representatives']=[dict(papers[r['entity_id']],entity_id=r['entity_id'],task=r['task'],cosine=float(r['cosine']),margin=float(r['margin']),needs_review=bool(int(r['needs_review']))) for r in chosen]
  c['representative_selection']='Up to three highest-center-similarity papers with available abstracts and distinct tasks, then a highest-center-similarity remaining task; not random or gold labels.'
 (a.draft/'evidence_clusters.json').write_text(json.dumps(cs,ensure_ascii=False,indent=2));print('Evidence enriched:',len(cs),'directions; membership unchanged')
if __name__=='__main__':main()
