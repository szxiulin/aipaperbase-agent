"""Small HTTP adapter for local research workflows; no model/network calls here."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote
from backend.research import store
from backend.collections import service as collections
from backend.catalog import queries
from backend.catalog.database import connect as catalog_connect
from backend.library import store as downloads, tasks
from backend.chats import store as chats


def canonical(catalog, ids):
    if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(x,str) or not x for x in ids): raise ValueError('一次请选择不超过 100 篇论文')
    mapping=collections.resolve_entity_ids(catalog,ids)
    if any(v['status']=='missing' for v in mapping.values()): raise ValueError('范围包含目录中不存在的论文')
    return list(dict.fromkeys(v['entity_id'] for v in mapping.values()))


def handle(handler, path, params=None, *, write=False):
    body=handler.read_json_body() if write else {}
    action=path.removeprefix('/api/research').strip('/')
    conn=handler._open_user_connection(read_only=not write)
    catalog=catalog_connect(handler.database_path,read_only=True)
    try:
        if not write and not action:
            handler.send_json({'items':store.list_records(conn)});return
        if not write and action=='backup':
            payload=store.export_backup(conn,handler.chats_path)
            handler.send_file(json.dumps(payload,ensure_ascii=False,indent=2).encode(),'application/json',filename='apex-personal.json',disposition='attachment');return
        if not write and action=='tasks':
            chat=handler._open_chats_connection(read_only=True)
            try:
                rows=chat.execute('SELECT task_id,conversation_id FROM ingest_tasks ORDER BY started_at DESC LIMIT 100').fetchall()
                items=[]
                for row in rows:
                    item=chats.get_ingest_task(chat,row['task_id'])
                    if item['status']=='running' and tasks.get(item['task_id']) is None:
                        item['status']='interrupted';item['error']='服务曾中断，可重试；已完成的阶段按当前状态跳过'
                    items.append(item)
                handler.send_json({'items':items});return
            finally:chat.close()
        if not write and action=='lookup':
            query=unquote((params or {}).get('q',[''])[0]).strip()
            for prefix in ['https://doi.org/','https://arxiv.org/abs/','https://arxiv.org/pdf/']:
                query=query.removeprefix(prefix)
            query=query.removesuffix('.pdf')
            exact=catalog.execute('''SELECT p.*,m.entity_id FROM paper_records p
                JOIN entity_memberships m ON m.record_id=p.record_id
                WHERE p.arxiv_id=? OR lower(p.doi)=lower(?) OR p.paper_id=? LIMIT 20''',
                (query,query,query)).fetchall() if query else []
            result={'items':[dict(r) for r in exact],'total':len(exact)} if exact else queries.papers(catalog, search=query, page=1, page_size=20)
            handler.send_json(result);return
        if write and action=='save':
            kind=body.get('kind');data=body.get('data',{})
            if kind in {'reading','chat_scope','comparison'}:
                ids=[data.get('entity_id')] if kind=='reading' else data.get('entity_ids',[])
                ids=canonical(catalog,ids)
                if kind=='reading':data['entity_id']=ids[0]
                else:data['entity_ids']=ids
            if kind=='watch':
                query=str(data.get('query','')).strip()
                if not query:raise ValueError('请输入要持续关注的检索条件')
                data['query']=query
                data['baseline']=queries.papers_entity_ids(catalog,search=query)['entity_ids']
            if kind=='comparison':
                if set(r.get('entity_id') for r in data.get('rows',[]))-set(data['entity_ids']):raise ValueError('比较表包含范围外论文')
            handler.send_json(store.save(conn,kind,data,body.get('record_id','')),201);return
        if write and action=='watch-check':
            record=next(r for r in store.list_records(conn) if r['record_id']==body.get('record_id') and r['kind']=='watch')
            now=queries.papers_entity_ids(catalog,search=record['data']['query'])['entity_ids']
            added=[eid for eid in now if eid not in record['data'].get('baseline',[])]
            handler.send_json({'entity_ids':added,'total':len(added),'query':record['data']['query']});return
        if write and action=='restore':
            handler.send_json(store.restore_backup(conn,body.get('backup'),handler.chats_path,confirm=body.get('confirm') is True));return
        if write and action=='upload':
            eid=canonical(catalog,[body.get('entity_id')])[0]
            content=base64.b64decode(body.get('content',''),validate=True)
            if not content.startswith(b'%PDF-') or len(content)>20*1024*1024:raise ValueError('请选择不超过 20 MB 的 PDF 文件')
            digest=hashlib.sha256(content).hexdigest()
            downloads.PAPERS_ROOT.mkdir(parents=True,exist_ok=True)
            dest=downloads.paper_path(eid)
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:
                raise ValueError('该论文已有不同 PDF，未覆盖原文件')
            if not dest.exists():
                with dest.open('xb') as file:file.write(content)
            db=downloads.connect()
            try:
                downloads.initialize(db)
                downloads.mark(db,eid,'success',source='local',sha256=digest,file_path=str(dest),bytes=len(content))
            finally:db.close()
            handler.send_json({'entity_id':eid,'status':'saved','message':'PDF 已保存；尚未解析或调用模型'});return
        raise ValueError('不支持的研究资料操作')
    finally:conn.close();catalog.close()
