import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DirectionsConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "config/directions.json").read_text(encoding="utf-8"))
        cls.areas = {a["id"] for a in json.loads((ROOT / "config/areas.json").read_text(encoding="utf-8"))["areas"]}
        cls.directions = cls.config["directions"]

    def test_ids_are_unique_and_areas_are_known(self):
        ids = [d["direction_id"] for d in self.directions]
        self.assertEqual(len(ids), len(set(ids)))
        for d in self.directions:
            self.assertIn(d["area"], self.areas, d["direction_id"])

    def test_every_direction_is_displayable(self):
        for d in self.directions:
            self.assertTrue(d["name_zh"].strip(), d["direction_id"])
            self.assertTrue(d["name_en"].strip(), d["direction_id"])
            self.assertTrue(d["definition"].strip(), d["direction_id"])
            self.assertIn(d["status"], {"confirmed", "pending_review"}, d["direction_id"])

    def test_an_alias_maps_to_at_most_one_direction_per_area(self):
        seen = {}
        for d in self.directions:
            for alias in d["aliases"]:
                key = (d["area"], alias)
                self.assertNotIn(key, seen, f"{alias!r}: {seen.get(key)} and {d['direction_id']}")
                seen[key] = d["direction_id"]

    def test_split_children_name_their_parent_and_stay_in_one_area(self):
        ids = {d["direction_id"] for d in self.directions}
        children = [d for d in self.directions if d["parent_direction_id"]]
        self.assertTrue(children)
        families = {}
        for d in children:
            self.assertNotIn(d["parent_direction_id"], ids, "a split parent must be replaced by its children")
            self.assertTrue(d["direction_id"].startswith(d["parent_direction_id"] + "-"))
            families.setdefault(d["parent_direction_id"], set()).add(d["area"])
        for parent, areas in families.items():
            self.assertEqual(len(areas), 1, parent)

    def test_source_records_the_draft_and_coverage(self):
        src = self.config["source"]
        self.assertEqual(src["areas_version"], "areas-v1.5")
        self.assertEqual(len(src["draft_id"]), 64)
        self.assertEqual(src["papers"] + src["non_paper_records"], 121742)


if __name__ == "__main__":
    unittest.main()
