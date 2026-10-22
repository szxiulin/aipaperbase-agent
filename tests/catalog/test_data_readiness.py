import unittest
from scripts.collect.data_readiness import classification_group, input_hash, text_title


class DataReadinessTests(unittest.TestCase):
    def test_missing_abstract_and_duplicate_candidate_are_separate(self):
        self.assertEqual(classification_group('Study', 'Abstract'), 'with_abstract')
        self.assertEqual(classification_group('Study', ''), 'title_only')
        self.assertEqual(classification_group('Study', 'Abstract', True), 'review_first')
        self.assertEqual(classification_group('Corrections to a study', 'Abstract'), 'review_first')

    def test_queue_hash_tracks_exact_model_input(self):
        self.assertEqual(text_title('<i>Method</i>: Study'), 'Method: Study')
        self.assertNotEqual(input_hash('Method', 'Old abstract'), input_hash('Method', 'New abstract'))
