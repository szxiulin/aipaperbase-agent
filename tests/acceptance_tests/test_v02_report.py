import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.labels_tests.test_import_labels import CREATED, make_db
from backend.catalog import labels as label_import

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_v02_report", ROOT / "scripts/acceptance/build_v02_report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

METRICS = {"n": 2, "area": {"accuracy": 1.0, "ci95": [0.3, 1.0], "correct": 2},
           "direction": {"strict_accuracy": 1.0, "conditional_accuracy": 1.0,
                         "macro_support2": {"f1": 1.0}, "macro_classes": 2},
           "other_share": {"gold": 0.0, "system": 0.0},
           "missing_abstract": {"n": 0}, "confusions": {"area": [], "direction": []}}


def fixture_db(directory: Path) -> tuple[Path, Path]:
    con, a, d, n, g, _ = make_db(directory)
    label_import.import_labels(con, "rel", CREATED, areas_labels=a, directions_labels=d,
                               non_paper_labels=n, golden=g, metrics=directory / "none.json")
    db = directory / "catalog.sqlite"
    with sqlite3.connect(db) as target:
        con.backup(target)
    con.close()
    metrics = directory / "metrics.json"
    metrics.write_text(json.dumps(METRICS))
    return db, metrics


class AcceptanceReportTest(unittest.TestCase):
    def test_report_renders_sections_and_passes_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            db, metrics = fixture_db(d)
            out = report.build(db, metrics, d / "report.html")
            html = out.read_text(encoding="utf-8")
        self.assertIn("v0.2 数据层验收", html)
        self.assertIn("领域分布", html)
        self.assertIn("数据一致性", html)
        self.assertNotIn(">失败<", html)
        self.assertIn("100.0%", html)

    def test_missing_assignment_blocks_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            db, metrics = fixture_db(d)
            with sqlite3.connect(db) as con:
                con.execute("DELETE FROM entity_area_assignments WHERE entity_id = 'e1'")
            with self.assertRaises(RuntimeError):
                report.build(db, metrics, d / "report.html")


if __name__ == "__main__":
    unittest.main()
