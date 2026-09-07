"""Propose a comparison artifact; never mark model output as user reviewed."""
from backend.agent.tools.base import ToolDef, ToolResult
from backend.research import store


def propose(ctx, title, fields, rows):
    allowed=ctx.get('allowed_entity_ids')
    if allowed is None or not allowed:raise ValueError('请从我的论文选择明确范围后开启研究对话')
    if not isinstance(fields,list) or not 1<=len(fields)<=8 or any(not isinstance(f,str) or not f for f in fields):raise ValueError('请给出 1 至 8 个比较字段')
    if not isinstance(rows,list) or not rows or {r.get('entity_id') for r in rows}-set(allowed):raise ValueError('比较内容超出研究范围')
    pipeline=ctx.get('pipeline');checked=[]
    for row in rows:
        eid=row['entity_id']
        chunks={c.id:c for c in pipeline.store.list_by_document(eid)} if pipeline else {}
        cells={}
        for f in fields:
            cell=row.get('cells',{}).get(f,{})
            evidence=[{'chunk_id':cid,'entity_id':eid,'text':chunks[cid].text,'section':chunks[cid].metadata.get('section','')}
                      for cid in cell.get('chunk_ids',[]) if cid in chunks]
            cells[f]={'value':str(cell.get('value','')) if evidence else '', 'evidence':evidence,'origin':'model','reviewed':False}
        meta=ctx['catalog_conn'].execute('SELECT canonical_title FROM paper_entities WHERE entity_id=?',(eid,)).fetchone()
        checked.append({'entity_id':eid,'title':meta[0] if meta else eid,'cells':cells})
    # Keep omitted papers visible instead of silently shrinking the comparison.
    for eid in allowed:
        if eid not in {r['entity_id'] for r in checked}:
            checked.append({'entity_id':eid,'title':eid,'cells':{f:{'value':'','evidence':[]} for f in fields}})
    conn=ctx.get('organization_draft_conn')
    if conn is None:conn=ctx['organization_draft_factory']();ctx['organization_draft_conn']=conn
    result=store.save(conn,'comparison',{'title':str(title),'fields':fields,'rows':checked,'entity_ids':allowed,'status':'draft','conversation_id':ctx.get('conversation_id','')})
    return ToolResult(True,{'record_id':result['record_id'],'status':'draft'},summary='比较草稿已保存到研究资料，请打开核对后保存；无证据字段留空')


def comparison_tools():
    return [ToolDef('propose_research_comparison','把明确研究范围内论文比较为可编辑草稿。先检索各论文证据。rows 每项包含 entity_id、cells；cells 按 fields 字段名对应 {value,chunk_ids:[真实证据ID]}。没有证据的字段留空。用户在研究资料中核对后确认保存。',
        {'type':'object','properties':{'title':{'type':'string'},'fields':{'type':'array','items':{'type':'string'}},'rows':{'type':'array','items':{'type':'object'}}},'required':['title','fields','rows']},propose,'research')]
