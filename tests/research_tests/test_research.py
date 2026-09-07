import sqlite3
import unittest
from backend.agent.tools.fulltext import _resolve_entity_ids, _get_evidence
from backend.agent.tools.organization import _check_scope
from backend.research import store
from backend.collections import database


class ResearchTests(unittest.TestCase):
    def test_scope_intersection_and_hard_limit(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.execute('CREATE TABLE collection_members(entity_id TEXT,collection_id TEXT)')
        conn.executemany('INSERT INTO collection_members VALUES (?,?)',[('p1','c'),('p2','c')])
        self.assertEqual(_resolve_entity_ids({'collections_conn':conn},['p1'],'c'),['p1'])
        self.assertEqual(_resolve_entity_ids({'allowed_entity_ids':['p1']},['p2'],None),[])
        self.assertEqual(_resolve_entity_ids({'allowed_entity_ids':[]},None,None),[])
        self.assertFalse(_get_evidence({'allowed_entity_ids':['p1']},'p2').ok)

    def test_negated_all_cannot_enlarge_scope(self):
        for query in ['不要把全部论文加入，只加入这两篇','only these, not all papers','只加入这两篇']:
            with self.assertRaises(ValueError):_check_scope({'query':query},True)
        _check_scope({'query':'把全部论文加入集合'},True)

    def test_research_records_are_read_only_until_written(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        self.assertEqual(store.list_records(conn),[])
        self.assertEqual(conn.execute('SELECT count(*) FROM sqlite_master').fetchone()[0],0)
        store.save(conn,'reading',{'entity_id':'p1','status':'reading'})
        store.save(conn,'reading',{'entity_id':'p1','status':'read'})
        self.assertEqual(len(store.list_records(conn)),1)
        self.assertEqual(store.list_records(conn)[0]['data']['status'],'read')

    def test_invalid_backup_rows_rollback_all_inserts(self):
        import tempfile
        from pathlib import Path
        conn=database.connect(Path(tempfile.mkdtemp())/'collections.sqlite');database.initialize(conn)
        backup={'format':'apex-personal-v1','tables':{'main.research_records':[
          {'record_id':'one','kind':'note','data_json':'{}','created_at':'now','updated_at':'now'},
          {'unknown_column':'bad'}]}}
        with self.assertRaises(ValueError):store.restore_backup(conn,backup,Path(tempfile.mkdtemp())/'chats.sqlite',confirm=True)
        self.assertEqual(store.list_records(conn),[])

    def test_backup_restores_notes_into_a_fresh_database(self):
        import tempfile
        from pathlib import Path
        root=Path(tempfile.mkdtemp())
        source=database.connect(root/'source.sqlite');database.initialize(source)
        store.save(source,'note',{'title':'Keep','content':'evidence snapshot'})
        payload=store.export_backup(source,root/'absent-chats.sqlite')
        target=database.connect(root/'target.sqlite');database.initialize(target)
        result=store.restore_backup(target,payload,root/'new-chats.sqlite',confirm=True)
        self.assertEqual(result['added'],1)
        self.assertEqual(store.list_records(target)[0]['data']['content'],'evidence snapshot')
        self.assertEqual(store.restore_backup(target,payload,root/'new-chats.sqlite',confirm=True)['added'],0)
