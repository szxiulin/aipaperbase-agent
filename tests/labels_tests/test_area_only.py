import csv
import tempfile
import unittest
from pathlib import Path

from backend.catalog import labels
from backend.catalog.metrics import corpus_summary
from backend.analytics import areas
from backend.capabilities import build_registry
from tests.labels_tests.test_import_labels import make_db, CREATED


class AreaOnlyTest(unittest.TestCase):
    def fixture(self, root):
        con, a, d, n, g, m = make_db(root)
        self.addCleanup(con.close)
        with d.open(newline='') as f:
            rows = list(csv.reader(f))
        with d.open('w', newline='') as f:
            csv.writer(f).writerows([r for r in rows if r[0] != 'e2'])
        pending = root / 'pending_directions.csv'
        pending.write_text('entity_id,status,reason\ne2,pending,area_only\n')
        kwargs = dict(areas_labels=a, directions_labels=d, non_paper_labels=n,
                      golden=g, metrics=m, pending_direction_labels=pending)
        return con, pending, kwargs

    def test_area_only_import_counts_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            con, pending, kwargs = self.fixture(Path(tmp))
            stats = labels.import_labels(con, 'rel', CREATED, **kwargs)
            self.assertEqual(stats['pending_directions'], 1)
            summary = corpus_summary(con)
            self.assertEqual(summary['classified_count'], 3)
            self.assertEqual(summary['pending_count'], 0)
            self.assertEqual(summary['direction_classified_count'], 2)
            self.assertEqual(summary['pending_direction_count'], 1)
            self.assertEqual(summary['direction_coverage_rate'], 66.67)
            overview = areas.areas_overview(con)
            self.assertEqual(overview['classified_entity_count'], 3)
            llm = next(a for a in overview['topics'] if a['topic_id'] == 'llm')
            self.assertEqual(llm['pending_direction_count'], 1)
            registry = build_registry()
            result = registry.call(con, 'search_papers', {'filters': {'pending_direction': True}})
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.data['total'], 1)
            self.assertEqual(result.data['items'][0]['entity_id'], 'e2')
            self.assertIsNone(result.data['items'][0]['direction_id'])
            self.assertEqual(registry.call(con, 'count_papers', {'filters': {'pending_direction': True}}).data['total'], 1)
            self.assertFalse(registry.call(con, 'count_papers', {'filters': {'pending_direction': 'true'}}).ok)

    def test_direction_pending_rejects_invalid_states_before_write(self):
        for content in ['e2,pending\ne2,pending\n', 'e2,done\n', 'e4,pending\n', 'missing,pending\n']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                con, pending, kwargs = self.fixture(Path(tmp))
                pending.write_text('entity_id,status\n' + content)
                with self.assertRaises(ValueError):
                    labels.import_labels(con, 'rel', CREATED, **kwargs)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM label_releases').fetchone()[0], 0)

    def test_missing_direction_requires_explicit_workflow_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            con, pending, kwargs = self.fixture(Path(tmp))
            pending.write_text('entity_id,status\n')
            with self.assertRaisesRegex(ValueError, 'non-paper mismatch'):
                labels.import_labels(con, 'rel', CREATED, **kwargs)
