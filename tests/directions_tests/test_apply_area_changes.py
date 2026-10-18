import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:  # maintainer-only script dependency
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("numpy not installed (maintainer dependency)")

ROOT = Path(__file__).resolve().parents[2]


def write_csv(path, fields, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class ApplyAreaChangesTest(unittest.TestCase):
    def test_only_changed_papers_move_and_task_match_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            draft, idx, vec = t / "draft", t / "idx", t / "vec"
            for d in (draft, idx, vec):
                d.mkdir()
            tasks = ["a task", "b task", "c task"]
            (t / "vocab.jsonl").write_text("".join(json.dumps({"task": x}) + "\n" for x in tasks))
            tv = np.eye(3, 1024, dtype=np.float16)
            np.save(vec / "tasks.1024.f16.npy", tv)
            papers = ["p1", "p2", "p3", "p4"]
            (idx / "ids.jsonl").write_text("".join(json.dumps({"entity_id": p}) + "\n" for p in papers))
            np.save(idx / "papers.1024.f16.npy", np.eye(4, 1024, k=10, dtype=np.float16))
            fields = ["entity_id", "area", "task", "draft_direction", "cosine", "margin", "needs_review", "area_confidence"]
            write_csv(draft / "paper_direction_draft.csv", fields, [
                {"entity_id": "p1", "area": "x", "task": "a task", "draft_direction": "x-d01", "cosine": 1, "margin": 1, "needs_review": 0, "area_confidence": "high"},
                {"entity_id": "p2", "area": "y", "task": "b task", "draft_direction": "y-d01", "cosine": 1, "margin": 1, "needs_review": 0, "area_confidence": "high"},
                {"entity_id": "p3", "area": "y", "task": "c task", "draft_direction": "y-d02", "cosine": 1, "margin": 1, "needs_review": 0, "area_confidence": "high"},
                {"entity_id": "p4", "area": "x", "task": "b task", "draft_direction": "x-d01", "cosine": 1, "margin": 1, "needs_review": 0, "area_confidence": "high"},
            ])
            base = {"representatives": [], "boundary_papers": [], "boundary_note": "", "name_zh": "n", "top_tasks": []}
            (draft / "named_clusters.json").write_text(json.dumps([dict(base, id=i, area=i[0]) for i in ("x-d01", "y-d01", "y-d02")]))
            (draft / "clustering_report.json").write_text(json.dumps({"source": {}}))
            write_csv(t / "labels.csv", ["entity_id", "area", "area_confidence"], [
                {"entity_id": "p1", "area": "x", "area_confidence": "high"}, {"entity_id": "p2", "area": "y", "area_confidence": "high"},
                {"entity_id": "p3", "area": "y", "area_confidence": "high"}, {"entity_id": "p4", "area": "y", "area_confidence": "medium"}])
            subprocess.run([sys.executable, str(ROOT / "scripts/directions/apply_area_changes.py"), "--draft", str(draft),
                            "--vocab", str(t / "vocab.jsonl"), "--task-vectors", str(vec), "--paper-index", str(idx),
                            "--labels", str(t / "labels.csv"), "--out", str(t / "out")], check=True, capture_output=True)
            out = {r["entity_id"]: r for r in csv.DictReader((t / "out/paper_direction_draft.csv").open())}
            counts = {c["id"]: c["paper_count"] for c in json.loads((t / "out/named_clusters.json").read_text())}
        self.assertEqual(out["p4"]["draft_direction"], "y-d01")  # same task "b task" already lives in y-d01
        self.assertEqual(out["p4"]["assigned_by"], "task_match")
        self.assertEqual(out["p4"]["area"], "y")
        self.assertEqual(out["p1"]["assigned_by"], "kept")
        self.assertEqual(counts, {"x-d01": 1, "y-d01": 2, "y-d02": 1})


if __name__ == "__main__":
    unittest.main()
