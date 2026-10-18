import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("render_review", ROOT / "scripts/directions/render_review.py")
render_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_review)


def write_draft(d: Path, names: dict[str, str], assignment: dict[str, str]) -> None:
    (d / "named_clusters.json").write_text(json.dumps([{"id": k, "name_zh": v} for k, v in names.items()]))
    with (d / "paper_direction_draft.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "draft_direction"])
        w.writerows(assignment.items())


def write_report(d: Path, labels: str | None = None) -> None:
    report = {"rows": 2, "unique_task_phrases": 2, "source": {"labels_sha256": "abc"},
              "clusters": [], "priority_review": False}
    if labels:
        report["area_update"] = {"labels": labels, "moved_papers": 1,
                                 "assigned_by": {"task_match": 1, "nearest_center": 0}}
    (d / "clustering_report.json").write_text(json.dumps(report))


class RenderPageTest(unittest.TestCase):
    def test_page_names_the_label_version_it_was_built_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_draft(d, {"a-d01": "甲"}, {"e1": "a-d01", "e2": "a-d02"})
            write_report(d, "areas-v1.4.csv")
            page = render_review.render(d).read_text()
            payload = json.loads(page.split('type="application/json">')[1].split("</script>")[0])
            expected = render_review.draft_ids(d)[0]
        self.assertIn("主领域依据 areas-v1.4.csv", page)
        self.assertNotIn("areas-v1.3", page)
        self.assertEqual(payload["draft_id"], expected)

    def test_older_draft_without_area_update_does_not_name_a_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_draft(d, {"a-d01": "甲"}, {"e1": "a-d01"})
            write_report(d)
            page = render_review.render(d).read_text()
        self.assertIn("主领域依据生成该草案时的标签版本", page)
        self.assertNotIn("areas-v1.3", page)


class DraftIdentityTest(unittest.TestCase):
    def test_renaming_keeps_draft_id_but_reassignment_changes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_draft(d, {"a-d01": "甲"}, {"e1": "a-d01", "e2": "a-d02"})
            first, legacy_first = render_review.draft_ids(d)
            write_draft(d, {"a-d01": "改名后"}, {"e2": "a-d02", "e1": "a-d01"})
            renamed, legacy_renamed = render_review.draft_ids(d)
            write_draft(d, {"a-d01": "改名后"}, {"e1": "a-d02", "e2": "a-d02"})
            moved, _ = render_review.draft_ids(d)
        self.assertEqual(first, renamed)  # row order and names do not matter
        self.assertNotEqual(first, moved)
        self.assertNotEqual(legacy_first, legacy_renamed)  # legacy id still tracks the old v1 hash

    def test_extra_legacy_ids_are_accepted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_draft(d, {"a-d01": "甲"}, {"e1": "a-d01"})
            own = render_review.draft_ids(d)[1][0]
            (d / "legacy_run_ids.json").write_text(json.dumps(["old1", own]))
            _, legacy = render_review.draft_ids(d)
        self.assertEqual(legacy, [own, "old1"])


if __name__ == "__main__":
    unittest.main()
