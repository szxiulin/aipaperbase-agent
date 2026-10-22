"""A conference presentation must not double-count the journal publication."""
import sqlite3
import unittest
from backend.analytics.areas import _year_series, _top_venues


class PublicationAttributionTests(unittest.TestCase):
    def test_year_and_venue_use_only_canonical_publication(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        try:
            c.executescript('''
                CREATE TABLE entity_area_assignments(entity_id, area_id, is_non_paper);
                CREATE TABLE area_definitions(area_id, name_zh);
                CREATE TABLE entity_memberships(entity_id, record_id, is_canonical);
                CREATE TABLE paper_records(record_id, year, venue, venue_type, list_status);
                INSERT INTO area_definitions VALUES ('ml', 'Machine learning');
                INSERT INTO entity_area_assignments VALUES ('kept-id', 'ml', 0);
                INSERT INTO entity_memberships VALUES ('kept-id', 'journal', 1), ('kept-id', 'presentation', 0);
                INSERT INTO paper_records VALUES ('journal', 2024, 'TMLR', 'journal', 'rolling'),
                                                  ('presentation', 2026, 'ICML', 'conference', 'final');
            ''')
            totals, areas = _year_series(c)
            self.assertEqual([(x['year'], x['total_entities']) for x in totals], [(2024, 1)])
            self.assertEqual([(x['year'], x['entity_count']) for x in areas], [(2024, 1)])
            venues = _top_venues(c)
            self.assertEqual([(x['venue'], x['entity_count']) for x in venues], [('TMLR', 1)])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM paper_records').fetchone()[0], 2)
        finally:
            c.close()
