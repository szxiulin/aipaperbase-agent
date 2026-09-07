"""Writable isolated QA. All personal paths are temporary; remote adapters fail closed.
Run with --root /tmp/apex-qa-UNIQUE --port 8767. No .env is read.
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from contextlib import ExitStack
from backend.api.server import Handler
from backend.catalog import database as catalog_db
from backend.collections import database as collections_db
from backend.chats import database as chats_db
from backend.library import store, downloader, sources
from backend.rag import parse_store, loader, service
from raglib import Pipeline, InMemoryStore, MarkdownSplitter
from tests.rag_tests.test_index_state import Embedder


def make_catalog(path):
    conn = catalog_db.connect(path)
    conn.executescript(catalog_db.SCHEMA)
    def insert(table, **values):
        columns = conn.execute(f'PRAGMA table_info({table})').fetchall()
        row = {c['name']: values.get(c['name'], 0 if c['type'] == 'INTEGER' else '') for c in columns}
        conn.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",list(row.values()))
    insert('data_releases',release_id='qa',created_at='2026-09-05',status='ready',record_count=3)
    insert('source_files',source_file='fixture',release_id='qa',year=2026)
    for i in range(1,4):
        insert('paper_records',record_id=f'r{i}',paper_id=f'p{i}',title=f'Fixture Paper {i}',normalized_title=f'fixture paper {i}',
               authors='Fixture',abstract='Synthetic QA content',venue='QA',year=2026,arxiv_id=f'2601.0000{i}',source_file='fixture',release_id='qa')
        insert('paper_entities',entity_id=f'p{i}',canonical_record_id=f'r{i}',canonical_title=f'Fixture Paper {i}',
               first_year=2026,last_year=2026,record_count=1,venue_count=1,entity_status='single',release_id='qa')
        insert('entity_memberships',record_id=f'r{i}',entity_id=f'p{i}',confidence='single',decision_status='auto_accepted',is_canonical=1,release_id='qa')
    conn.commit();conn.close()


class Extraction:
    def __init__(self, path):
        self.markdown = f'# {path.stem}\n\nSynthetic evidence: visual planning agent performs image restoration. {path.stem}'
    def save_markdown(self,path,with_images=True):
        dest=Path(path);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(self.markdown)


def install(stack, root):
    root.mkdir(parents=True,exist_ok=True)
    catalog_path=root/'catalog.sqlite'
    if not catalog_path.exists(): make_catalog(catalog_path)
    for module, path in [(catalog_db,catalog_path),(collections_db,root/'user/collections.sqlite'),
                          (chats_db,root/'user/chats.sqlite'),(store,root/'papers/downloads.sqlite'),(parse_store,root/'parsed/parsed.sqlite')]:
        stack.enter_context(patch.object(module,'DEFAULT_DATABASE',path))
    stack.enter_context(patch.object(store,'PAPERS_ROOT',root/'papers'))
    stack.enter_context(patch.object(parse_store,'PARSED_ROOT',root/'parsed'))
    for name in ['raglib.config.load_dotenv','backend.rag.service.load_dotenv']:
        stack.enter_context(patch(name,lambda *a,**k:None))
    stack.enter_context(patch.dict(os.environ,{},clear=True))
    def blocked(*a,**k): raise AssertionError('External network forbidden in fixture')
    stack.enter_context(patch('httpx.Client.request',blocked))
    stack.enter_context(patch('urllib.request.urlopen',blocked))
    stack.enter_context(patch.object(sources,'remote_arxiv_candidates',blocked))
    stack.enter_context(patch.object(sources,'remote_openalex_candidates',blocked))
    pipeline=Pipeline(MarkdownSplitter(),Embedder(),InMemoryStore())
    stack.enter_context(patch.object(service,'_pipeline',pipeline))
    stack.enter_context(patch.object(downloader,'download_pdf',lambda url,**k: (b'%PDF-1.4\n'+url.encode()+b'\n'+b'fixture '*200, 1650)))
    stack.enter_context(patch.object(loader,'extract_batch',lambda paths:[Extraction(p) for p in paths]))
    def model(client,*,messages,**kwargs):
        # Only provider output is stubbed. Runner, tool dispatch, persisted chat,
        # draft, confirmation HTTP API and SQLite writes are production code.
        start=max(i for i,m in enumerate(messages) if m['role']=='user')
        query=messages[start]['content']
        recent=messages[start+1:]
        tool_messages=[m for m in recent if m['role']=='tool']
        def call(name,args):
            return {'choices':[{'message':{'tool_calls':[{'id':f'qa{len(tool_messages)}','type':'function',
                    'function':{'name':name,'arguments':json.dumps(args)}}]}}]}
        if '入库' in query and not tool_messages:
            return call('ingest_papers',{'entity_ids':['p1','p2']})
        if '比较' in query:
            if len(tool_messages)==0:return call('get_evidence',{'entity_id':'p1'})
            if len(tool_messages)==1:return call('get_evidence',{'entity_id':'p2'})
            if len(tool_messages)==2:
                return call('propose_research_comparison',{'title':'视觉 Agent 比较草稿','fields':['方法','局限'],
                    'rows':[{'entity_id':eid,'cells':{'方法':{'value':'视觉规划 Agent 用于图像恢复','chunk_ids':[pipeline.store.list_by_document(eid)[0].id]},'局限':{'value':'未经证实的断言','chunk_ids':['fake']}}} for eid in ['p1','p2']]})
        if '判断' in query:
            if len(tool_messages)==0: return call('list_collections',{})
            if len(tool_messages)==1: return call('get_evidence',{'entity_id':'p1'})
            if len(tool_messages)==2: return call('get_evidence',{'entity_id':'p2'})
            if len(tool_messages)==3:
                conn=collections_db.connect(read_only=True)
                collection=dict(conn.execute('SELECT * FROM collections ORDER BY created_at LIMIT 1').fetchone());conn.close()
                decisions=[]
                for eid in ['p1','p2']:
                    decisions.append({'entity_id':eid,'collection_id':collection['collection_id'], 'decision':'include', 'confidence':0.9,
                        'rule':collection['description'],'reason':'Fixture model: visual agent matches the collection rule.',
                        'evidence':[{'entity_id':eid,'chunk_id':pipeline.store.list_by_document(eid)[0].id}]})
                return call('propose_collection_assignments',{'collection_ids':[collection['collection_id']], 'entity_ids':['p1','p2'],'decisions':decisions})
        return {'choices':[{'message':{'content':'隔离模型已生成草稿，请在卡片核对并确认。'},'finish_reason':'stop'}]}
    stack.enter_context(patch('backend.agent.runner.llm_chat',model))
    class FixtureHandler(Handler):
        database_path=catalog_path
        collections_path=collections_db.DEFAULT_DATABASE
        chats_path=chats_db.DEFAULT_DATABASE
    return FixtureHandler


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    with ExitStack() as stack:
        handler=install(stack,args.root)
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler)
        print(f'Isolated writable QA http://127.0.0.1:{args.port}; data={args.root}',flush=True)
        try: server.serve_forever()
        finally: server.server_close()
