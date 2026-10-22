import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.catalog import milestone


class MilestoneTest(unittest.TestCase):
    def test_report_requires_matching_ready_catalog(self):
        con=sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        con.execute('CREATE TABLE data_releases (release_id TEXT, status TEXT, created_at TEXT)')
        con.execute("INSERT INTO data_releases VALUES ('current','ready','2026-10-22')")
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp)/'milestone.json'
            with patch.object(milestone,'REPORT',report):
                self.assertIsNone(milestone.current_milestone(con))
                report.write_text(json.dumps({'catalog_release_id':'old','version':'M1'}))
                self.assertIsNone(milestone.current_milestone(con))
                report.write_text(json.dumps({'catalog_release_id':'current','version':'M1'}))
                self.assertEqual(milestone.current_milestone(con)['version'],'M1')

    def test_rebuilt_catalog_requires_matching_source_and_label_hashes(self):
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        con.executescript("""
            CREATE TABLE data_releases (release_id TEXT, status TEXT, created_at TEXT, source_digest TEXT);
            CREATE TABLE label_releases (catalog_release_id TEXT, status TEXT, created_at TEXT, areas_sha256 TEXT, directions_sha256 TEXT);
            INSERT INTO data_releases VALUES ('rebuilt','ready','2026-10-23','source');
            INSERT INTO label_releases VALUES ('rebuilt','ready','2026-10-23','areas','directions');
        """)
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'milestone.json'
            payload = {'catalog_release_id': 'original', 'version': 'M1', 'content_identity':
                       {'source_digest': 'source', 'areas_sha256': 'areas', 'directions_sha256': 'directions'}}
            report.write_text(json.dumps(payload))
            with patch.object(milestone, 'REPORT', report):
                self.assertEqual(milestone.current_milestone(con)['version'], 'M1')
                for table, field in [('data_releases','source_digest'), ('label_releases','areas_sha256'), ('label_releases','directions_sha256')]:
                    con.execute(f"UPDATE {table} SET {field}='changed'")
                    self.assertIsNone(milestone.current_milestone(con))
                    con.rollback()
