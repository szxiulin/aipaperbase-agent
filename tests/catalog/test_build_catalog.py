"""Unit tests for the catalog collector helpers.

Covers:
- ``is_rolling(venue, year)`` — list_status / verification_status computation;
- ``normalize_authors`` — HF parquet numpy-repr author cleanup;
- ``text_content`` — block-aware XML text join (ACL title whitespace fix).

Assertions use offsets relative to the build year so the tests stay valid
whenever they are run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.collect.build_catalog import (  # noqa: E402
    CURRENT_YEAR,
    VENUE_FINAL_EXCEPTIONS,
    is_rolling,
    normalize_authors,
    text_content,
)
from lxml import etree  # noqa: E402


class IsRollingTest(unittest.TestCase):
    def test_historical_years_are_final(self) -> None:
        self.assertFalse(is_rolling("CVPR", CURRENT_YEAR - 1))
        self.assertFalse(is_rolling("NeurIPS", CURRENT_YEAR - 2))
        self.assertFalse(is_rolling("TPAMI", CURRENT_YEAR - 3))

    def test_current_year_defaults_to_rolling(self) -> None:
        # venues not registered as early-final still roll in the current year (NeurIPS meets in Dec; journals publish year-round)
        self.assertTrue(is_rolling("NeurIPS", CURRENT_YEAR))
        self.assertTrue(is_rolling("TPAMI", CURRENT_YEAR))
        self.assertTrue(is_rolling("EMNLP", CURRENT_YEAR))

    def test_future_years_are_rolling(self) -> None:
        # early-release scenario: a paper for a 2027 conference is released this year, the list is not final -> rolling
        self.assertTrue(is_rolling("AAAI", CURRENT_YEAR + 1))
        self.assertTrue(is_rolling("CVPR", CURRENT_YEAR + 1))

    def test_registered_final_exceptions_are_final(self) -> None:
        # registered early-final venue x year: even in an in-progress year it is marked final
        for venue, years in VENUE_FINAL_EXCEPTIONS.items():
            for year in years:
                with self.subTest(venue=venue, year=year):
                    self.assertFalse(is_rolling(venue, year))

    def test_exceptions_do_not_leak_to_other_years(self) -> None:
        # The override table only affects the registered year:
        # the previous year is already complete -> final; the next year is not registered -> rolling
        for venue in VENUE_FINAL_EXCEPTIONS:
            self.assertFalse(is_rolling(venue, CURRENT_YEAR - 1))
            self.assertTrue(is_rolling(venue, CURRENT_YEAR + 1))

    def test_unknown_venue_in_current_year_is_rolling(self) -> None:
        self.assertTrue(is_rolling("UNKNOWN_VENUE", CURRENT_YEAR))
        self.assertFalse(is_rolling("UNKNOWN_VENUE", CURRENT_YEAR - 1))


class NormalizeAuthorsTest(unittest.TestCase):
    """HF ai-conferences parquet stores numpy arrays; their str() form is
    "['A' 'B']" (no commas). normalize_authors must yield '; '-joined names."""

    def test_numpy_repr_no_commas(self) -> None:
        self.assertEqual(
            normalize_authors("['Kou Misaki' 'Takuya Akiba']"),
            "Kou Misaki; Takuya Akiba",
        )

    def test_numpy_repr_three_names(self) -> None:
        self.assertEqual(
            normalize_authors("['Liang Lv' 'Di Wang' 'Jing Zhang']"),
            "Liang Lv; Di Wang; Jing Zhang",
        )

    def test_numpy_repr_unicode_names(self) -> None:
        self.assertEqual(
            normalize_authors("['Georg Von der Brüggen' 'Jian-Jia Chen']"),
            "Georg Von der Brüggen; Jian-Jia Chen",
        )

    def test_double_quote_numpy_repr(self) -> None:
        self.assertEqual(normalize_authors('["A" "B"]'), "A; B")

    def test_list_style_repr_with_commas(self) -> None:
        self.assertEqual(normalize_authors("['A', 'B', 'C']"), "A; B; C")

    def test_plain_string_passthrough(self) -> None:
        self.assertEqual(normalize_authors("Single Author"), "Single Author")
        self.assertEqual(normalize_authors(""), "")

    def test_none_and_scalar(self) -> None:
        self.assertEqual(normalize_authors(None), "")
        self.assertEqual(normalize_authors(123), "123")


class TextContentTest(unittest.TestCase):
    """ACL Anthology titles embed <fixed-case> inline tags. Naive
    ' '.join(itertext()) inserted spaces at tag boundaries ("O cto T ools");
    the block-aware join must not, while keeping block-level separators."""

    def test_inline_fixed_case_no_space(self) -> None:
        root = etree.fromstring(
            b"<title>O<fixed-case>cto</fixed-case>T<fixed-case>ools</fixed-case>: A <i>Study</i></title>"
        )
        self.assertEqual(text_content(root), "OctoTools: A Study")

    def test_block_tags_keep_separator(self) -> None:
        root = etree.fromstring(b"<div><p>First para</p><p>Second para</p></div>")
        self.assertEqual(text_content(root), "First para Second para")

    def test_inline_tag_inside_paragraph(self) -> None:
        root = etree.fromstring(
            b"<abstract><p>We propose <i>Diff</i>usion models.</p></abstract>"
        )
        self.assertEqual(text_content(root), "We propose Diffusion models.")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(text_content(None), "")


if __name__ == "__main__":
    unittest.main()
