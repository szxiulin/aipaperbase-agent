import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("label_papers", ROOT / "scripts/label/label_papers.py")
label_papers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(label_papers)
CFG = json.loads((ROOT / "config/areas.json").read_text(encoding="utf-8"))
AREA_IDS = {a["id"] for a in CFG["areas"]}


class NormalizeTest(unittest.TestCase):
    def test_valid_label_is_kept_and_lists_are_trimmed(self):
        fields, status = label_papers.normalize({
            "area": "cv_lowlevel", "area_secondary": "cv_lowlevel", "area_confidence": "HIGH",
            "task": "Image Super-Resolution", "method_family": ["diffusion", "", "a", "b", "c", "d"],
            "datasets": "DIV2K", "modality": ["image", "smell"], "contribution_type": "method",
        }, AREA_IDS, CFG)
        self.assertEqual(status, "ok")
        self.assertEqual(fields["area_secondary"], "")  # same as primary is dropped
        self.assertEqual(fields["task"], "image super-resolution")
        self.assertEqual(fields["method_family"], ["diffusion", "a", "b", "c"])
        self.assertEqual(fields["datasets"], ["DIV2K"])
        self.assertEqual(fields["modality"], ["image"])

    def test_unknown_area_is_flagged(self):
        fields, status = label_papers.normalize({"area": "biology", "contribution_type": "novel"}, AREA_IDS, CFG)
        self.assertEqual(status, "invalid_area")
        self.assertEqual(fields["area"], "")
        self.assertEqual(fields["contribution_type"], "")

    def test_prompt_lists_every_area(self):
        prompt = label_papers.build_system_prompt(CFG)
        for area_id in AREA_IDS:
            self.assertIn(f"- {area_id}:", prompt)


class IngestTest(unittest.TestCase):
    def test_external_labels_are_validated_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "labels.jsonl", Path(tmp) / "out.csv"
            src.write_text("\n".join([
                json.dumps({"entity_id": "e1", "area": "llm", "method_family": ["transformer"],
                            "contribution_type": "method"}),
                json.dumps({"entity_id": "e1", "area": "nlp"}),
                "not json",
                json.dumps({"entity_id": "e2", "area": "made_up"}),
            ]), encoding="utf-8")
            label_papers.ingest([src], out, "codex-test", AREA_IDS, CFG)
            with out.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["entity_id"] for r in rows], ["e1", "e2"])
        self.assertEqual(rows[0]["area"], "llm")
        self.assertEqual(rows[0]["method_family"], "transformer")
        self.assertEqual(rows[0]["model"], "codex-test")
        self.assertEqual(rows[1]["status"], "invalid_area")


class ExportTest(unittest.TestCase):
    def test_sharded_export_splits_papers_and_writes_prompt_once(self):
        papers = [{"entity_id": f"e{i}", "venue": "ICML", "year": 2025, "title": "t", "abstract": ""}
                  for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            label_papers.export(Path(tmp), papers, "PROMPT", shard_size=2)
            shards = sorted(p.name for p in Path(tmp).glob("shard_*"))
            counts = [len((Path(tmp) / s / "papers.jsonl").read_text().splitlines()) for s in shards]
            first = json.loads((Path(tmp) / "shard_0001/papers.jsonl").read_text().splitlines()[0])
            self.assertEqual((Path(tmp) / "labeling_prompt.md").read_text(), "PROMPT\n")
        self.assertEqual(shards, ["shard_0001", "shard_0002", "shard_0003"])
        self.assertEqual(counts, [2, 2, 1])
        self.assertEqual(first["abstract"], "(no abstract available)")


class KappaTest(unittest.TestCase):
    def test_kappa_bounds(self):
        spec = importlib.util.spec_from_file_location("compare_labels", ROOT / "scripts/label/compare_labels.py")
        compare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compare)
        self.assertAlmostEqual(compare.cohen_kappa([("a", "a"), ("b", "b")]), 1.0)
        self.assertAlmostEqual(compare.cohen_kappa([("a", "b"), ("b", "a")]), -1.0)


if __name__ == "__main__":
    unittest.main()


class ShardIoTest(unittest.TestCase):
    def label(self, entity_id):
        return json.dumps(dict(entity_id=entity_id, area='llm', area_secondary='', area_confidence='high',
                               task='language model evaluation', method_family=[], datasets=[], modality=['text'],
                               application='', contribution_type='method'))

    def run_io(self, *args):
        import subprocess
        import sys
        return subprocess.run([sys.executable, str(ROOT / "scripts/label/shard_io.py"), *args],
                              capture_output=True, text=True)

    def test_next_skips_labeled_and_status_flags_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            shard = Path(tmp)
            (shard / "papers.jsonl").write_text("\n".join(json.dumps(
                {"entity_id": f"e{i}", "venue": "ICML", "year": 2025, "title": f"T{i}", "abstract": "A"})
                for i in range(3)), encoding="utf-8")
            (shard / "labels.jsonl").write_text(self.label('e0')+'\n', encoding="utf-8")
            shown = self.run_io("next", tmp, "1").stdout
            self.assertIn("## e1 |", shown)
            self.assertNotIn("## e0 |", shown)
            self.assertEqual(self.run_io("status", tmp).returncode, 1)
            (shard / "labels.jsonl").write_text('\n'.join(self.label(f'e{i}') for i in range(3))+'\n',
                                                encoding="utf-8")
            self.assertEqual(self.run_io("status", tmp).returncode, 0)
            with (shard / "labels.jsonl").open("a", encoding="utf-8") as f:
                f.write(self.label('e2')+'\n')
            result = self.run_io("status", tmp)
            self.assertEqual(result.returncode, 1)
            self.assertIn("duplicate", result.stdout)


class MergeTest(unittest.TestCase):
    def test_override_replaces_only_ok_rows(self):
        import subprocess
        import sys
        fields = ["entity_id", "area", "model", "status"]
        with tempfile.TemporaryDirectory() as tmp:
            base, over, out = (Path(tmp) / n for n in ("base.csv", "over.csv", "out.csv"))
            for path, rows in ((base, [["e1", "trustworthy_ai", "codex", "ok"], ["e2", "llm", "codex", "ok"]]),
                               (over, [["e1", "llm", "deepseek", "ok"], ["e2", "nlp", "deepseek", "invalid_area"]])):
                with path.open("w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(fields)
                    w.writerows(rows)
            subprocess.run([sys.executable, str(ROOT / "scripts/label/merge_labels.py"), str(base), str(over),
                            "--out", str(out)], check=True, capture_output=True)
            with out.open(newline="") as f:
                merged = {r["entity_id"]: r for r in csv.DictReader(f)}
        self.assertEqual(merged["e1"]["area"], "llm")
        self.assertEqual(merged["e1"]["model"], "deepseek")
        self.assertEqual(merged["e2"]["area"], "llm")


class AreaOnlyTest(unittest.TestCase):
    def test_area_only_keeps_base_fields_and_marks_prompt(self):
        base = {"entity_id": "e1", "task": "image super-resolution", "method_family": "diffusion",
                "datasets": "DIV2K", "modality": "image", "application": "", "contribution_type": "method"}
        fake = {"content": json.dumps({"area": "cv_lowlevel", "area_secondary": "", "area_confidence": "high",
                                       "task": "SHOULD NOT BE USED"}),
                "model": "m", "prompt_tokens": 1, "cached_tokens": 0, "completion_tokens": 1}
        seen = {}

        def fake_call(gen, system, paper, retries=3, context=""):
            seen["context"] = context
            return fake

        original = label_papers.call_llm
        label_papers.call_llm = fake_call
        try:
            row = label_papers.label_one(None, "S", {"entity_id": "e1"}, AREA_IDS, CFG, base)
        finally:
            label_papers.call_llm = original
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["area"], "cv_lowlevel")
        self.assertEqual(row["task"], "image super-resolution")
        self.assertEqual(row["datasets"], "DIV2K")
        self.assertEqual(row["prompt_version"], label_papers.AREA_ONLY_PROMPT_VERSION)
        self.assertIn("image super-resolution", seen["context"])

    def test_area_prompt_lists_rules_but_not_task_field(self):
        prompt = label_papers.build_area_prompt(CFG)
        self.assertIn("Face anti-spoofing", prompt)
        self.assertNotIn('"task":', prompt)
