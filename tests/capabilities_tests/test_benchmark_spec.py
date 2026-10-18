import json
import unittest
from pathlib import Path

from backend.capabilities import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "data/benchmarks/analysis_questions_v1.json"


class BenchmarkSpecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC.read_text(encoding="utf-8"))
        cls.questions = cls.spec["questions"]

    def test_thirty_unique_questions(self):
        self.assertEqual(len(self.questions), 30)
        ids = [q["id"] for q in self.questions]
        self.assertEqual(len(ids), len(set(ids)))

    def test_each_question_targets_a_registered_capability(self):
        names = set(REGISTRY.names())
        for q in self.questions:
            self.assertIn(q["capability"], names, q["id"])
            self.assertIn(q["check"], {"scalar", "rows", "pairs", "compare_totals", "compare_year",
                                       "field", "taxonomy"}, q["id"])
            self.assertTrue(q["sql"].strip(), q["id"])

    def test_sql_references_only_the_entity_basis(self):
        for q in self.questions:
            sql = q["sql"]
            self.assertNotIn("entity_area_assignments", sql, q["id"])  # ground truth uses the `base` CTE
            self.assertTrue(sql.upper().startswith("SELECT"), q["id"])


if __name__ == "__main__":
    unittest.main()
