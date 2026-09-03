from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from backend.library import planner


CATALOG = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


def build_catalog_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper_entities (
            entity_id TEXT PRIMARY KEY, canonical_record_id TEXT, canonical_title TEXT
        );
        CREATE TABLE paper_records (
            record_id TEXT PRIMARY KEY, authors TEXT, paper_url TEXT, doi TEXT,
            arxiv_id TEXT, pdf_url TEXT, venue TEXT, year INTEGER
        );
        CREATE TABLE entity_memberships (entity_id TEXT, record_id TEXT);
        """
    )
    records = [
        # record_id, authors, paper_url, doi, arxiv_id, pdf_url, venue, year
        ("r1", "Alice", "https://a", "10.1/a", "2401.00001v2", "", "CVPR", 2024),
        ("r2", "Bob", "https://b", "10.1/b", "", "https://openaccess.thecvf.com/content/CVPR2024/papers/x.pdf", "CVPR", 2024),
        ("r3", "Carol", "https://c", "10.1/c", "", "https://ieeexplore.ieee.org/document/123", "TPAMI", 2023),
        ("r4", "Dave", "https://d", "10.1/d", "", "https://example.com/paper.pdf", "ICML", 2025),
        ("r5", "Eve", "https://e", "10.1/e", "", "", "AAAI", 2023),
        ("r6", "Frank", "https://f", "10.1/f", "2402.00002", "https://ieeexplore.ieee.org/document/456", "TOG", 2023),
    ]
    connection.executemany("INSERT INTO paper_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
    entities = [
        ("e1", "r1", "Paper One"),
        ("e2", "r2", "Paper Two"),
        ("e3", "r3", "Paper Three"),
        ("e4", "r4", "Paper Four"),
        ("e5", "r5", "Paper Five"),
        ("e6", "r6", "Paper Six"),
    ]
    connection.executemany("INSERT INTO paper_entities VALUES (?, ?, ?)", entities)
    memberships = [("e1", "r1"), ("e2", "r2"), ("e3", "r3"), ("e4", "r4"), ("e5", "r5"), ("e6", "r6")]
    connection.executemany("INSERT INTO entity_memberships VALUES (?, ?)", memberships)
    return connection


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog_fixture()

    def tearDown(self) -> None:
        self.catalog.close()

    def test_source_classification_and_priority(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        by_id = {item["entity_id"]: item for item in plan["items"]}
        self.assertEqual(by_id["e1"]["source"], "arxiv")
        self.assertEqual(by_id["e1"]["download_url"], "https://arxiv.org/pdf/2401.00001")
        self.assertEqual(by_id["e2"]["source"], "open")
        self.assertEqual(by_id["e3"]["source"], "restricted")
        self.assertEqual(by_id["e4"]["source"], "unspecified")
        self.assertEqual(by_id["e5"]["source"], "none")
        # arxiv_id takes priority over the restricted pdf_url
        self.assertEqual(by_id["e6"]["source"], "arxiv")
        self.assertEqual(by_id["e6"]["download_url"], "https://arxiv.org/pdf/2402.00002")

    def test_summary_counts_and_size_estimate(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        summary = plan["summary"]
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["counts"], {
            "arxiv": 2, "open": 1, "restricted": 1, "unspecified": 1, "none": 1,
        })
        self.assertEqual(summary["downloadable"], 3)
        self.assertEqual(
            summary["estimated_total_bytes"],
            2 * planner.ARXIV_ESTIMATED_BYTES + 1 * planner.OPEN_ESTIMATED_BYTES,
        )

    def test_downloadable_items_have_url(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        for item in plan["items"]:
            if item["downloadable"]:
                self.assertTrue(item["download_url"])
                self.assertGreater(item["estimated_bytes"], 0)
            else:
                self.assertEqual(item["download_url"], "")
                self.assertEqual(item["estimated_bytes"], 0)

    def test_input_dedup(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e1", "e2"])
        self.assertEqual(plan["summary"]["total"], 2)

    def test_empty_input(self) -> None:
        plan = planner.build_plan(self.catalog, [])
        self.assertEqual(plan["summary"]["total"], 0)
        self.assertEqual(plan["items"], [])


class RealCatalogPlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CATALOG.exists():
            raise unittest.SkipTest("catalog.sqlite 尚未构建")

    def test_plan_against_real_catalog(self) -> None:
        catalog = sqlite3.connect(CATALOG)
        catalog.row_factory = sqlite3.Row
        try:
            merged = catalog.execute(
                "SELECT entity_id FROM paper_entities WHERE entity_status = 'merged' ORDER BY entity_id LIMIT 20"
            ).fetchall()
            self.assertTrue(merged)
            ids = [row["entity_id"] for row in merged]
            plan = planner.build_plan(catalog, ids)
            self.assertEqual(plan["summary"]["total"], len(ids))
            self.assertTrue(all(item["title"] for item in plan["items"]))
            self.assertEqual(
                sum(plan["summary"]["counts"].values()), len(ids)
            )
            self.assertEqual(
                sum(1 for item in plan["items"] if item["downloadable"]),
                plan["summary"]["downloadable"],
            )
        finally:
            catalog.close()


if __name__ == "__main__":
    unittest.main()
