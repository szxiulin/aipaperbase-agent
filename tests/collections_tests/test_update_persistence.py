import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.collections import database, service


class UpdatePersistenceTest(unittest.TestCase):
    def test_update_survives_connection_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'collections.sqlite'
            conn = database.connect(path)
            database.initialize(conn)
            original = service.create_collection(conn, name='before', description='old')
            result = service.update_collection(conn, original['collection_id'], name='after', description='new')
            self.assertEqual(result['name'], 'after')
            conn.close()
            reopened = database.connect(path, read_only=True)
            try:
                saved = service.collection_summary(reopened, original['collection_id'])
                self.assertEqual((saved['name'], saved['description']), ('after', 'new'))
            finally:
                reopened.close()

    def test_name_and_description_update_atomically(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        database.initialize(conn)
        original = service.create_collection(conn, name='before', description='old')
        conn.execute("""CREATE TRIGGER reject_description BEFORE UPDATE OF description ON collections
                        BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            service.update_collection(conn, original['collection_id'], name='after', description='new')
        saved = service.collection_summary(conn, original['collection_id'])
        self.assertEqual((saved['name'], saved['description']), ('before', 'old'))

    def test_empty_name_does_not_change_description(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        database.initialize(conn)
        original = service.create_collection(conn, name='before', description='old')
        with self.assertRaises(ValueError):
            service.update_collection(conn, original['collection_id'], name=' ', description='new')
        self.assertEqual(service.collection_summary(conn, original['collection_id'])['description'], 'old')
