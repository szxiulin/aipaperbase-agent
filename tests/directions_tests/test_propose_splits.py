import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("propose_splits", ROOT / "scripts/directions/propose_splits.py")
propose_splits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(propose_splits)


def group(direction: str, text: str) -> str:
    return propose_splits.group_of(text, propose_splits.GROUPS[direction])[0]


class GroupingTest(unittest.TestCase):
    def test_priority_order_prefers_the_specific_group(self):
        self.assertEqual(group("trustworthy_ai-d04", "face anti-spoofing with forgery cues"), "face_antispoof")
        self.assertEqual(group("cv_lowlevel-d15", "real-world video super-resolution"), "video_sr")
        self.assertEqual(group("llm-d11", "speculative decoding for diffusion language models"), "diffusion_lm")

    def test_substrings_do_not_create_false_matches(self):
        # "detection" contains "ct"; "interface" contains "face"; neither is a special-imaging or face paper.
        self.assertEqual(group("cv_lowlevel-d15", "object detection benchmark"), "unmatched")
        self.assertEqual(group("cv_lowlevel-d15", "interface design for displays"), "unmatched")

    def test_common_phrasings_land_in_the_expected_group(self):
        self.assertEqual(group("trustworthy_ai-d04", "multimodal fake news detection"), "misinformation")
        self.assertEqual(group("trustworthy_ai-d04", "image manipulation localization"), "forgery_localization")
        self.assertEqual(group("cv_lowlevel-d11", "single image deraining"), "derain")
        self.assertEqual(group("cv_lowlevel-d11", "real-world image dehazing"), "dehaze")
        self.assertEqual(group("cv_lowlevel-d15", "single image super-resolution"), "generic_sr")


if __name__ == "__main__":
    unittest.main()
