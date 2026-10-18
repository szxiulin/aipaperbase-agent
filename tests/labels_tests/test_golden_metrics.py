import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/label" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoldenMetricsTest(unittest.TestCase):
    def test_allocation_keeps_total_and_floor(self):
        sample = load("golden_sample")
        quota = sample.allocate({"a": 1000, "b": 200, "c": 30}, 50, 5)
        self.assertEqual(sum(quota.values()), 50)
        self.assertTrue(all(v >= 5 for v in quota.values()))
        self.assertEqual(set(quota), {"a", "b", "c"})

    def test_wilson_interval_is_bounded_and_centered(self):
        evaluate = load("golden_evaluate")
        low, high = evaluate.wilson(90, 100)
        self.assertLess(low, 0.9)
        self.assertGreater(high, 0.9)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)
        self.assertEqual(evaluate.wilson(0, 0), [0.0, 1.0])

    def test_prf_perfect_and_errors(self):
        evaluate = load("golden_evaluate")
        perfect = evaluate.prf(["a", "a", "b", "b"], ["a", "a", "b", "b"])
        self.assertEqual(perfect["micro"]["f1"], 1.0)
        self.assertEqual(perfect["macro_classes"], 2)
        wrong = evaluate.prf(["a", "b"], ["b", "a"])
        self.assertEqual(wrong["micro"]["f1"], 0.0)
        self.assertEqual(wrong["macro_support2"]["f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
