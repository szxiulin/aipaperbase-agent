import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("flag_records", ROOT / "scripts/directions/flag_records.py")
flag_records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flag_records)

NON_PAPERS = {
    "Editorial Board": "front_matter",
    "Table of Contents": "front_matter",
    "IEEE Transactions on Image Processing publication information": "front_matter",
    "Preface": "front_matter",
    "VIS 2024 Program Committee": "committee_or_reviewers",
    "IEEE ISMAR 2023 Science &amp; Technology Program Committee Members for Journal Papers": "committee_or_reviewers",
    "2025 Reviewers List <sup>*</sup>": "committee_or_reviewers",
    "2024 VGTC Visualization Service Award": "award_or_statement",
    "Guest Editorial: Special Issue on Visual Datasets": "editorial",
    "Pre-Trained Models for Search and Recommendation: Introduction to the Special Issue—Part 2": "editorial",
    "Message from the Editor-in-Chief": "editorial",
    "Erratum: Wavelet Fluids": "correction_notice",
    "Correction to: Learning to Adapt to Light": "correction_notice",
    "Notice of Retraction: CHN: an efficient algorithm": "retraction",
}
REAL_PAPERS = [
    "Preface: A Data-driven Volumetric Prior for Few-shot Ultra High-resolution Face Synthesis",
    "PrefAce: Face-Centric Pretraining with Self-Structure Aware Distillation",
    "Reconfiguring Proportional Committees",
    "Approval-based committee voting under incomplete information",
    "Optimization without Retraction on the Random Generalized Stiefel Manifold",
    "Welcome to the Dark Side: Analyzing the Revenue Flows of Fraud in the Online Ad Ecosystem.",
    "Low-rank Variational Bayes correction to the Laplace method",
    "Analyzing the Use of Metaphors in News Editorials for Political Framing",
    "A Gold Standard Dataset for the Reviewer Assignment Problem",
    "MarioGPT: Open-Ended Text2Level Generation through Large Language Models",
]


class FlagRecordsTest(unittest.TestCase):
    def test_front_matter_and_notices_are_flagged(self):
        for title, kind in NON_PAPERS.items():
            self.assertEqual(flag_records.classify(title), kind, title)

    def test_real_papers_with_lookalike_titles_are_not_flagged(self):
        for title in REAL_PAPERS:
            self.assertEqual(flag_records.classify(title), "", title)


if __name__ == "__main__":
    unittest.main()
