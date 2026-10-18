import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.catalog import labels as label_import
from backend.catalog.database import ROOT, initialize
from backend.analytics import areas

CREATED = "2026-10-18T00:00:00Z"


def make_db(directory: Path):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    initialize(con)
    con.execute("INSERT INTO data_releases VALUES ('rel', ?, 'digest', 1, 4, 'ready')", (CREATED,))
    con.execute("INSERT INTO source_files VALUES ('f.csv', 'rel', 'sha', 4, 'CVPR', 'conference', 2024, 'final')")
    records = [
        ("r1", "CVPR", 2023, "Paper One"), ("r2", "ICML", 2024, "Paper Two"),
        ("r3", "ACL", 2024, "Paper Three"), ("r4", "CVPR", 2024, "Front Matter"),
    ]
    for rid, venue, year, title in records:
        con.execute(
            """INSERT INTO paper_records (record_id, paper_id, venue, venue_type, year, track, title,
                   normalized_title, authors, abstract, abstract_source_name, abstract_source_url,
                   abstract_source_tier, abstract_fetched_at, doi, arxiv_id, paper_url, pdf_url, source_name,
                   source_url, source_tier, verification_status, list_status, fetched_at, source_file,
                   imported_at, release_id)
               VALUES (?, ?, ?, ?, ?, '', ?, ?, '', '', '', '', '', '', '', '', '', '', '', '', '', '', 'final', ?, 'f.csv', ?, 'rel')""",
            (rid, rid, venue, "conference" if venue != "ACL" else "conference", year, title, title.lower(), CREATED, CREATED))
        con.execute("INSERT INTO paper_entities VALUES (?, ?, ?, ?, ?, 1, 1, 'single', ?, 'rel')",
                    (f"e{rid[1:]}", rid, title, year, year, CREATED))
        con.execute("INSERT INTO entity_memberships VALUES (?, ?, 'single', '', 'single', 'auto_accepted', 1, ?, 'rel')",
                    (rid, f"e{rid[1:]}", CREATED))

    areas_csv = directory / "areas.csv"
    with areas_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "area", "area_secondary", "area_confidence", "task", "method_family",
                    "datasets", "modality", "application", "contribution_type", "model", "prompt_version",
                    "areas_version", "prompt_tokens", "cached_tokens", "completion_tokens", "status", "labeled_at"])
        for eid, area in [("e1", "ml_foundations"), ("e2", "llm"), ("e3", "other"), ("e4", "other")]:
            w.writerow([eid, area, "", "high", "task", "", "", "", "", "method", "m", "p", "areas-v1.5", 0, 0, 0, "ok", CREATED])
    directions_csv = directory / "directions.csv"
    with directions_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "area", "direction_id", "assigned_by", "cosine", "margin", "needs_review"])
        w.writerow(["e1", "ml_foundations", "ml_foundations-d01", "kept", "0.9", "0.1", 0])
        w.writerow(["e2", "llm", "llm-d01", "kept", "0.8", "0.1", 1])
        w.writerow(["e3", "other", "other-d01", "kept", "", "", 0])
    non_paper = directory / "non_paper.csv"
    with non_paper.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "record_kind"])
        w.writerow(["e4", "front_matter"])
    golden = directory / "golden.csv"
    with golden.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "sample_order", "system_area", "system_direction", "pred_area",
                    "pred_direction", "gold_area", "gold_direction", "verdict", "notes"])
        w.writerow(["e1", 1, "ml_foundations", "ml_foundations-d01", "ml_foundations", "ml_foundations-d01",
                    "ml_foundations", "ml_foundations-d01", "agree_system", ""])
        w.writerow(["e2", 2, "llm", "llm-d01", "llm", "llm-d01", "llm", "llm-d01", "agree_system", ""])
    metrics = directory / "metrics.json"
    metrics.write_text(json.dumps({"n": 2, "area": {"accuracy": 1.0}}))
    return con, areas_csv, directions_csv, non_paper, golden, metrics


class LabelImportTest(unittest.TestCase):
    def test_imports_assignments_and_excludes_non_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            con, a, d, n, g, m = make_db(Path(tmp))
            stats = label_import.import_labels(con, "rel", CREATED, areas_labels=a, directions_labels=d,
                                               non_paper_labels=n, golden=g, metrics=m)
            self.assertEqual(stats["area_assignments"], 4)
            self.assertEqual(stats["direction_assignments"], 3)
            self.assertEqual(stats["non_paper"], 1)
            self.assertEqual(stats["golden_samples"], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM entity_direction_assignments").fetchone()[0], 3)

            overview = areas.areas_overview(con)
            self.assertEqual(overview["classified_entity_count"], 3)
            self.assertEqual(overview["non_paper_count"], 1)
            ml = next(x for x in overview["topics"] if x["topic_id"] == "ml_foundations")
            self.assertEqual(ml["entity_count"], 1)
            self.assertEqual(sum(len(x["subtopics"]) for x in overview["topics"]), 383)

            detail = areas.area_detail(con, "ml_foundations")
            self.assertEqual(detail["metrics"]["entity_count"], 1)
            self.assertEqual(detail["subtopics"][0]["topic_id"], "ml_foundations-d01")

            papers = areas.papers(con, area_id="ml_foundations")
            self.assertEqual(papers["total"], 1)
            self.assertEqual(papers["items"][0]["entity_id"], "e1")
            self.assertEqual(areas.papers(con, direction_id="llm-d01")["items"][0]["entity_id"], "e2")
            years = {y["year"]: y["total_entities"] for y in areas.area_trends(con)["years"]}
            self.assertEqual(years, {2023: 1, 2024: 2})
            evaluation = areas.label_evaluation(con)
            self.assertEqual(evaluation["metrics"]["n"], 2)
            self.assertEqual(len(evaluation["samples"]), 2)

    def test_rejects_direction_outside_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            con, a, d, n, g, m = make_db(Path(tmp))
            rows = list(csv.reader(d.open()))
            rows[1][1] = "llm"  # e1 direction stays ml_foundations-d01
            with d.open("w", newline="") as f:
                csv.writer(f).writerows(rows)
            # the area file still says ml_foundations, so the two files now disagree
            with self.assertRaises(ValueError):
                label_import.import_labels(con, "rel", CREATED, areas_labels=a, directions_labels=d,
                                           non_paper_labels=n, golden=g, metrics=m)


if __name__ == "__main__":
    unittest.main()
