"""Lossless identity views, stored artifacts, vector scopes and repeatable rebuilds."""
import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.catalog import identity
from backend.research import store as research
from backend.library import store as downloads, local_status
from backend.rag import parse_store, service, evidence
from backend.chats import store as chats, database as chat_db
from backend.agent.tools import fulltext
from raglib import InMemoryStore, MarkdownSplitter, Pipeline


class IdentityCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='identity-compat-'))
        self.alias_file = self.root / 'aliases.csv'
        self.write_aliases({})
        self.patches = [patch.object(identity, 'ALIASES_PATH', self.alias_file),
                        patch.object(downloads, 'PAPERS_ROOT', self.root / 'papers'),
                        patch.object(parse_store, 'PARSED_ROOT', self.root / 'parsed')]
        for p in self.patches: p.start()
        self.connections = []

    def tearDown(self):
        for c in self.connections: c.close()
        for p in reversed(self.patches): p.stop()

    def db(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
        self.connections.append(c); return c

    def write_aliases(self, mapping):
        with self.alias_file.open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['alias_entity_id', 'entity_id']); w.writerows(mapping.items())

    def test_reading_update_scope_and_backup_preserve_raw_records(self):
        c = self.db()
        research.save(c, 'reading', {'entity_id':'old','status':'read'})
        research.save(c, 'chat_scope', {'conversation_id':'chat','entity_ids':['old','new']})
        research.save(c, 'note', {'content':'old is literal prose','evidence':[{'entity_id':'old','text':'source'}]})
        before = [tuple(r) for r in c.execute('select * from research_records')]
        self.write_aliases({'old':'new'})
        view = research.list_records(c)
        self.assertEqual(next(r for r in view if r['kind']=='reading')['data']['entity_id'], 'new')
        self.assertEqual(research.chat_scope(c,'chat')['entity_ids'], ['new'])
        self.assertEqual(next(r for r in view if r['kind']=='note')['data']['content'], 'old is literal prose')
        self.assertEqual(before, [tuple(r) for r in c.execute('select * from research_records')])
        backup = research.export_backup(c, self.root/'absent-chat.sqlite')
        self.assertEqual(backup['tables']['main.research_records'][0]['record_id'], 'reading:old')
        research.save(c, 'reading', {'entity_id':'new','status':'reading'})
        self.assertEqual(c.execute("select count(*) from research_records where kind='reading'").fetchone()[0],1)
        self.assertEqual(c.execute("select record_id from research_records where kind='reading'").fetchone()[0],'reading:old')
        restored=self.db(); research.restore_backup(restored,backup,self.root/'restored-chat.sqlite',confirm=True)
        self.assertEqual(research.chat_scope(restored,'chat')['entity_ids'], ['new'])
        self.assertEqual(research.list_records(restored)[-1]['data']['entity_id'], 'new')
        self.assertEqual(research.restore_backup(restored,backup,self.root/'restored-chat.sqlite',confirm=True)['added'],0)

    def test_conflicting_reading_states_remain_in_backup(self):
        c=self.db();research.save(c,'reading',{'entity_id':'old','status':'read'})
        research.save(c,'reading',{'entity_id':'new','status':'unread'})
        self.write_aliases({'old':'new'})
        self.assertEqual(len(research.list_records(c)),1)
        self.assertEqual(research.list_records(c)[0]['data']['status'],'unread')
        research.save(c,'reading',{'entity_id':'old','status':'reading'})
        self.assertEqual(c.execute('select count(*) from research_records').fetchone()[0],2)
        self.assertEqual(len(research.export_backup(c,self.root/'absent')['tables']['main.research_records']),2)

    def test_chat_citations_and_snapshots_are_views(self):
        c=self.db();chat_db.initialize(c)
        conversation=chats.create_conversation(c,'Fixture')
        msg=chats.add_message(c,conversation['conversation_id'],'assistant','Literal old text',chunks=[{'entity_id':'old','text':'quote'}])
        raw=c.execute('select chunks_json from messages').fetchone()[0]
        snapshot=chats.create_snapshot(c,conversation['conversation_id'],label='saved',source_message_id=msg['message_id'],messages=[msg])
        snapshot_raw=c.execute('select messages_json from message_snapshots').fetchone()[0]
        self.write_aliases({'old':'new'})
        current=chats.get_message(c,msg['message_id'])
        self.assertEqual(current['chunks'][0]['entity_id'],'new')
        self.assertEqual(current['content'],'Literal old text')
        self.assertEqual(c.execute('select chunks_json from messages').fetchone()[0],raw)
        from backend.chats import service as chat_service
        restored=chat_service.restore_snapshot(c,conversation_id=conversation['conversation_id'],snapshot_id=snapshot['snapshot_id'])
        self.assertEqual(chats.list_messages(c,restored['conversation_id'])[0]['chunks'][0]['entity_id'],'new')
        self.assertEqual(c.execute('select messages_json from message_snapshots').fetchone()[0],snapshot_raw)

    def test_pdf_markdown_vectors_remain_at_old_storage_keys(self):
        from tests.rag_tests.test_index_state import Embedder
        catalog=self.db();catalog.executescript("CREATE TABLE paper_entities(entity_id TEXT,canonical_title TEXT); INSERT INTO paper_entities VALUES ('old','Title');")
        d=self.db();downloads.initialize(d);p=self.db();parse_store.initialize(p)
        pdf=downloads.paper_path('old');pdf.parent.mkdir(parents=True);pdf.write_bytes(b'%PDF-fixture')
        md=parse_store.parsed_path('old');md.parent.mkdir(parents=True);md.write_text('# Title\n\nEvidence')
        downloads.mark(d,'old','success',file_path=str(pdf))
        parse_store.mark(p,'old','success',markdown_path=str(md),char_count=20)
        embedder=Embedder();pipeline=Pipeline(MarkdownSplitter(),embedder,InMemoryStore())
        service.ingest_parsed(catalog,p,['old'],pipeline=pipeline)
        original_calls=embedder.calls; original_pdf=pdf.read_bytes();original_md=md.read_bytes()
        failed_md=self.root/'parsed/new/new.md';failed_md.parent.mkdir();failed_md.write_text('failed parse output')
        parse_store.mark(p,'new','failed',markdown_path=str(failed_md))
        catalog.execute("UPDATE paper_entities SET entity_id='new'");self.write_aliases({'old':'new'})
        self.assertTrue(downloads.is_success(d,'new'));self.assertTrue(parse_store.is_parsed(p,'new'))
        self.assertEqual(downloads.paper_path('new'),pdf);self.assertEqual(parse_store.parsed_path(identity.storage_id(p,'parsed_documents','new')),md)
        self.assertEqual(downloads.status_map(d,['new'])['new'],'success')
        self.assertEqual(parse_store.status_map(p,['new'])['new'],'success')
        result=service.ingest_parsed(catalog,p,['new'],pipeline=pipeline)
        self.assertEqual(result['already_indexed'],1);self.assertEqual(embedder.calls,original_calls)
        self.assertEqual(pipeline.store.document_ids(),{'old'})
        chunks=pipeline.store.list_by_document('old');self.assertEqual(evidence.chunk_citation(chunks[0])['entity_id'],'new')
        snapshot=local_status.snapshot(catalog,downloads_conn=d,parsed_conn=p,document_chunk_counts={'old':len(chunks)},pipeline_fingerprint=service.pipeline_fingerprint(pipeline))
        self.assertEqual(snapshot['items'][0]['entity_id'],'new');self.assertEqual(set(snapshot['items'][0]['storage_entity_ids']),{'old','new'})
        self.assertEqual(snapshot['items'][0]['index_status'],'indexed_current')
        result=fulltext._search_evidence({'pipeline':pipeline,'allowed_entity_ids':['new']},'Evidence',entity_ids=['old'])
        self.assertTrue(result.ok);self.assertGreater(result.data['total'],0)
        self.assertTrue(all(r['entity_id']=='new' for r in result.data['items']))
        self.assertEqual(pdf.read_bytes(),original_pdf);self.assertEqual(md.read_bytes(),original_md)
        self.assertEqual(fulltext._search_evidence({'pipeline':pipeline,'allowed_entity_ids':['other']},'Evidence',entity_ids=['old']).data['total'],0)
        old_chunks=[(c.id,c.text) for c in chunks]
        new_pdf=self.root/'papers/new.pdf';new_pdf.write_bytes(b'%PDF-other-version')
        new_md=self.root/'parsed/new/new.md';new_md.parent.mkdir(exist_ok=True);new_md.write_text('# Other\n\nDifferent evidence')
        downloads.mark(d,'new','success',file_path=str(new_pdf))
        parse_store.mark(p,'new','success',markdown_path=str(new_md),char_count=25)
        service.ingest_parsed(catalog,p,['new'],pipeline=pipeline)
        self.assertEqual([(c.id,c.text) for c in pipeline.store.list_by_document('old')],old_chunks)
        self.assertEqual(pipeline.store.document_ids(),{'old','new'})
        self.assertEqual(pdf.read_bytes(),original_pdf)
        counts={eid:len(pipeline.store.list_by_document(eid)) for eid in ('old','new')}
        snapshot=local_status.snapshot(catalog,downloads_conn=d,parsed_conn=p,document_chunk_counts=counts,pipeline_fingerprint=service.pipeline_fingerprint(pipeline))
        self.assertEqual(len(snapshot['items']),1)
        self.assertEqual(set(snapshot['items'][0]['storage_entity_ids']),{'old','new'})
        self.assertEqual(len(snapshot['items'][0]['artifacts']),2)


    def test_http_pdf_can_read_old_artifact_by_new_id(self):
        from backend.api.server import Handler
        from unittest.mock import Mock
        self.write_aliases({'old':'new'})
        path=self.root/'old.pdf';path.write_bytes(b'%PDF-fixture')
        db_path=self.root/'downloads.sqlite'
        d=downloads.connect(db_path);downloads.initialize(d)
        downloads.mark(d,'old','success',file_path=str(path));d.close()
        handler=Mock()
        with patch.object(downloads,'DEFAULT_DATABASE',db_path):
            Handler._handle_download_pdf(handler,'new')
        self.assertEqual(handler.send_file.call_args.args[0],b'%PDF-fixture')
        handler.send_error_json.assert_not_called()

    def test_removing_one_manifest_never_targets_an_alias_file(self):
        d=self.db();downloads.initialize(d)
        old=self.root/'papers/old.pdf';old.parent.mkdir();old.write_bytes(b'%PDF-old-version')
        downloads.mark(d,'old','success',file_path=str(old))
        downloads.mark(d,'new','success',file_path=str(self.root/'papers/new.pdf'))
        self.write_aliases({'old':'new'})
        with patch.object(Path,'unlink',autospec=True) as unlink:
            downloads.remove(d,'new')
        self.assertNotIn(old,[call.args[0] for call in unlink.call_args_list])
        self.assertEqual(old.read_bytes(),b'%PDF-old-version')
        self.assertIsNotNone(d.execute("select 1 from downloads where entity_id='old'").fetchone())

    def test_alias_chain_and_cycle(self):
        self.write_aliases({'old':'middle','middle':'new'})
        self.assertEqual(identity.canonical('old'),'new')
        self.write_aliases({'old':'new','new':'old'})
        with self.assertRaises(ValueError): identity.aliases()

    def test_persistent_alias_is_loaded_on_every_rebuild(self):
        from tests.catalog.test_entity_identity_guard import EntityIdentityGuardTests
        self.write_aliases({'old-b':'old-a'})
        fixture=EntityIdentityGuardTests()
        for previous in ({'a':'old-a','b':'old-b'},{'a':'old-a','b':'old-a'}):
            c,result=fixture.build(previous,alias_snapshot=self.alias_file)
            self.connections.append(c)
            self.assertEqual(result['entities'],1)
            self.assertEqual(dict(c.execute('select alias_entity_id,entity_id from entity_aliases')),{'old-b':'old-a'})
