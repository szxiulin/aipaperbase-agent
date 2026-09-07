import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from tests.frontend.serve_workflow_fixture import install
from backend.collections import database as collections_db
from backend.rag import service


class WorkflowHTTPTest(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='apex-http-'))
        self.stack=ExitStack()
        handler=install(self.stack,self.root)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.stack.close()
    def request(self,path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        conn.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers={'Content-Type':'application/json'})
        response=conn.getresponse();data=json.loads(response.read());conn.close()
        return response.status,data
    def job(self,path,body):
        status,data=self.request(path,body);self.assertEqual(status,202,data)
        for _ in range(100):
            _,state=self.request('/api/downloads/status?task_id='+data['task_id'])
            if state['status'] not in ('running','pending'): return state
            time.sleep(.02)
        self.fail('fixture job timeout')
    def test_blank_read_is_side_effect_free_and_write_initializes(self):
        for path in ['/api/collections','/api/chats','/api/local-library','/api/my-library','/api/downloads','/api/rag/status']:
            status,data=self.request(path);self.assertEqual(status,200,data)
        self.assertEqual(self.request('/api/rag/status')[1]['status'],'unconfigured')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['catalog.sqlite'])
        with patch.object(service,'_pipeline',None),patch('backend.rag.service.QdrantStore',side_effect=AssertionError('must not construct')):
            self.assertEqual(service.index_reconcile_snapshot(),(None,''))
            self.assertEqual(service.ingested_document_ids(),set())
        from raglib.config import load_config
        config=load_config({'QDRANT_URL':'http://127.0.0.1:6333','QDRANT_COLLECTION':'missing'})
        with patch.object(service,'_pipeline',None),patch.object(service,'load_config',return_value=config),patch('qdrant_client.QdrantClient') as client:
            client.return_value.collection_exists.return_value=False
            self.assertEqual(service.index_reconcile_snapshot(),(None,''))
            client.return_value.create_collection.assert_not_called()
        self.assertEqual(self.request('/api/collections',{'name':'First'})[0],201)
        self.assertTrue((self.root/'user/collections.sqlite').exists())
    def test_ingest_chat_draft_confirm_restore_repeat_and_range(self):
        _,chat=self.request('/api/chats',{'title':'QA'})
        cid=chat['conversation_id']
        state=self.job(f'/api/chats/{cid}/ask',{'query':'入库这两篇 fixture','mode':'agent'})
        self.assertEqual(state['status'],'done',state)
        state=self.job(f'/api/chats/{cid}/ingest',{'entity_ids':['p1','p2']})
        self.assertEqual(state['status'],'done',state)
        self.assertEqual(len(self.request('/api/local-library')[1]['items']),2)
        state=self.job(f'/api/chats/{cid}/ask',{'query':'把本地库所有论文加入 CV Agent','mode':'agent'})
        self.assertEqual(state['status'],'done',state)
        messages=self.request(f'/api/chats/{cid}/messages')[1]['messages']
        run_id=messages[-1]['tool_trace'][0]['organization_run_id']
        plan=self.request('/api/organization-runs/'+run_id)[1]
        self.assertEqual(plan['scope'],['p1','p2'])
        selected=plan['suggestions'][:1]
        status,_=self.request('/api/organization/apply',{'run_id':run_id,'selected':[{'entity_id':'p3','collection_id':selected[0]['collection_id']}]})
        self.assertEqual(status,400)
        conn=collections_db.connect()
        conn.executescript("CREATE TRIGGER reject_qa BEFORE INSERT ON collection_members BEGIN SELECT RAISE(ABORT,'fixture failure'); END;")
        conn.close()
        body={'run_id':run_id,'selected':selected}
        self.assertEqual(self.request('/api/organization/apply',body)[0],500)
        self.assertEqual(self.request('/api/organization-runs/'+run_id)[1]['status'],'draft')
        self.assertEqual(self.request('/api/collections')[1]['items'],[])
        conn=collections_db.connect();conn.execute('DROP TRIGGER reject_qa');conn.close()
        self.assertEqual(self.request('/api/organization/apply',body)[1]['added'],1)
        self.assertEqual(self.request('/api/organization/apply',body)[1]['added'],0)
        saved=self.request('/api/organization-runs/'+run_id)[1]
        self.assertEqual(saved['status'],'applied');self.assertEqual(len(saved['result']['details']),1)
        conn=collections_db.connect(read_only=True)
        self.assertEqual([r[0] for r in conn.execute('SELECT entity_id FROM collection_members')],['p1']);conn.close()

    def test_catalog_assignment_is_saved_without_acquiring_fulltext(self):
        status,plan=self.request('/api/organization/plan',{'operation':'assign','entity_ids':['p3'],'target_name':'Metadata only'})
        self.assertEqual(status,201,plan)
        self.assertEqual(plan['scope'],['p3'])
        self.assertEqual(self.request('/api/local-library')[1]['items'],[])
        status,result=self.request('/api/organization/apply',{'run_id':plan['run_id'],'selected':plan['suggestions']})
        self.assertEqual(status,200,result)
        self.assertEqual(result['added'],1)
        items=self.request('/api/my-library')[1]['items']
        self.assertEqual([item['entity_id'] for item in items],['p3'])
        self.assertEqual(items[0]['title'],'Fixture Paper 3')
        self.assertEqual(len(items[0]['collections']),1)
        self.assertEqual(self.request('/api/local-library')[1]['items'],[])
        self.assertFalse((self.root/'papers').exists())
        self.assertFalse((self.root/'parsed').exists())

    def test_research_reading_notes_scope_comparison_backup(self):
        self.assertEqual(self.request('/api/research/lookup?q=https%3A%2F%2Farxiv.org%2Fpdf%2F2601.00003.pdf')[1]['items'][0]['entity_id'],'p3')
        self.assertEqual(self.request('/api/research')[1]['items'],[])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['catalog.sqlite'])
        for kind,data in [('reading',{'entity_id':'p1','status':'read'}),('note',{'title':'Evidence','content':'verified note','evidence':[{'entity_id':'p1','text':'source'}]}),('comparison',{'title':'Compare','entity_ids':['p1'],'fields':['method'],'rows':[{'entity_id':'p1','cells':{'method':{'value':'test'}}}]})]:
            status,result=self.request('/api/research/save',{'kind':kind,'data':data});self.assertEqual(status,201,result)
        _,chat=self.request('/api/chats',{'title':'Scoped'})
        self.assertEqual(self.request('/api/research/save',{'kind':'chat_scope','data':{'conversation_id':chat['conversation_id'],'entity_ids':['p1']}})[0],201)
        self.assertEqual(len(self.request('/api/research')[1]['items']),4)
        _,backup=self.request('/api/research/backup')
        self.assertEqual(backup['format'],'apex-personal-v1')
        preview=self.request('/api/research/restore',{'backup':backup})[1]
        self.assertEqual(preview['counts']['main.research_records'],4)
        result=self.request('/api/research/restore',{'backup':backup,'confirm':True})[1]
        self.assertEqual(result['added'],0,result)
        self.assertNotIn('.env',str(backup))
        status,_=self.request('/api/research/save',{'kind':'chat_scope','data':{'conversation_id':chat['conversation_id'],'entity_ids':['outside']}})
        self.assertEqual(status,400)

    def test_local_pdf_import_does_not_parse_or_overwrite(self):
        import base64
        def upload(value):return self.request('/api/research/upload',{'entity_id':'p3','content':base64.b64encode(value).decode()})
        self.assertEqual(upload(b'not a pdf')[0],400)
        status,result=upload(b'%PDF-1.4\nlocal test fixture');self.assertEqual(status,200,result)
        self.assertFalse((self.root/'parsed').exists())
        self.assertEqual(upload(b'%PDF-1.4\nlocal test fixture')[0],200)
        self.assertEqual(upload(b'%PDF-1.4\ndifferent')[0],400)
        self.assertEqual((self.root/'papers/p3.pdf').read_bytes(),b'%PDF-1.4\nlocal test fixture')
        self.assertEqual(len(self.request('/api/local-library')[1]['items']),1)

    def test_watch_and_interrupted_task_are_readable(self):
        _,record=self.request('/api/research/save',{'kind':'watch','data':{'query':'Fixture'}})
        self.assertEqual(self.request('/api/research/watch-check',{'record_id':record['record_id']})[1]['total'],0)
        _,chat=self.request('/api/chats',{'title':'Interrupted'})
        from backend.chats import database as db,store
        conn=db.connect();store.create_ingest_task(conn,'lost-task',chat['conversation_id'],['p1']);conn.close()
        tasks=self.request('/api/research/tasks')[1]['items']
        self.assertEqual(tasks[0]['status'],'interrupted')

    def test_scoped_agent_comparison_draft_review_and_restore(self):
        _,chat=self.request('/api/chats',{'title':'Compare'})
        cid=chat['conversation_id']
        self.assertEqual(self.job(f'/api/chats/{cid}/ingest',{'entity_ids':['p1','p2']})['status'],'done')
        self.request('/api/research/save',{'kind':'chat_scope','data':{'conversation_id':cid,'entity_ids':['p1','p2']}})
        result=self.job(f'/api/chats/{cid}/ask',{'query':'比较这两篇论文','mode':'agent'})
        self.assertEqual(result['status'],'done',result)
        records=self.request('/api/research')[1]['items']
        draft=next(r for r in records if r['kind']=='comparison')
        self.assertEqual(draft['data']['status'],'draft')
        self.assertEqual(draft['data']['entity_ids'],['p1','p2'])
        self.assertTrue(draft['data']['rows'][0]['cells']['方法']['evidence'])
        self.assertEqual(draft['data']['rows'][0]['cells']['局限']['value'],'')
        draft['data']['rows'][0]['cells']['方法']['value']='User reviewed method'
        draft['data']['status']='confirmed'
        self.assertEqual(self.request('/api/research/save',draft)[0],201)
        saved=next(r for r in self.request('/api/research')[1]['items'] if r['record_id']==draft['record_id'])
        self.assertEqual(saved['data']['rows'][0]['cells']['方法']['value'],'User reviewed method')
