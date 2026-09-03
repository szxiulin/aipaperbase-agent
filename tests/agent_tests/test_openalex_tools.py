from __future__ import annotations

import unittest
from unittest import mock

from backend.agent.tools.research.openalex import (
    _openalex_get_author,
    _openalex_get_work,
    _openalex_search,
    _venue_parse,
    openalex_tools,
)

CTX: dict = {}


class _WithFreshCtx(unittest.TestCase):
    def setUp(self) -> None:
        global CTX
        CTX = {}


def _fake_work(oid="W1", title="Attention Is All You Need", year=2017, venue="Advances in Neural Information Processing Systems",
               doi="10.5555/3295222.3295349", cited=90000, authors=("Vaswani", "Shazeer"), abstract=None):
    inv = None
    if abstract:
        inv = {}
        for i, w in enumerate(abstract.split()):
            inv.setdefault(w, []).append(i)
    return {
        "id": f"https://openalex.org/{oid}",
        "title": title,
        "publication_year": year,
        "primary_location": {"source": {"display_name": venue}},
        "doi": f"https://doi.org/{doi}",
        "cited_by_count": cited,
        "authorships": [{"author": {"display_name": a}} for a in authors],
        "abstract_inverted_index": inv,
        "open_access": {"is_oa": True},
    }


class VenueParseTests(_WithFreshCtx):
    def test_journal_abbr_to_source_id(self):
        filters, note = _venue_parse("TIP")
        self.assertEqual(filters, ["locations.source.id:S4210173141"])
        self.assertIsNone(note)

    def test_journal_full_name_unknown(self):
        # Full name not in the static table -> not a conference -> returns a note but no filter
        filters, note = _venue_parse("IEEE Transactions on Image Processing")
        self.assertEqual(filters, [])
        self.assertIsNotNone(note)

    def test_issn_direct(self):
        filters, note = _venue_parse("1057-7149")
        self.assertEqual(filters, ["locations.source.issn:1057-7149"])
        self.assertIsNone(note)

    def test_conference_no_filter_with_note(self):
        filters, note = _venue_parse("CVPR")
        self.assertEqual(filters, [])
        self.assertIn("按届拆分", note)

    def test_empty(self):
        filters, note = _venue_parse("")
        self.assertEqual(filters, [])
        self.assertIsNone(note)


class SearchTests(_WithFreshCtx):
    def test_search_with_query_and_venue(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={
            "meta": {"count": 2},
            "results": [
                _fake_work(),
                _fake_work(oid="W2", title="Second Paper", year=2018, venue="ICLR", doi="10.2/x", cited=10,
                           authors=("A", "B"), abstract="we propose a method for image super resolution"),
            ],
        }) as mocked:
            result = _openalex_search(CTX, query="attention", filters={"venue": "TIP"}, top_k=2)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 2)
        self.assertEqual(len(result.data["items"]), 2)
        # venue parsing should produce a source id filter
        call_args = mocked.call_args[0]
        self.assertIn("locations.source.id:S4210173141", call_args[1]["filter"])
        self.assertEqual(call_args[1]["search"], "attention")
        self.assertEqual(result.data["items"][0]["openalex_id"], "W1")
        # abstract inverted index restored
        self.assertIn("image super resolution", result.data["items"][1]["abstract"])
        # provenance
        self.assertEqual(result.provenance[0]["source"], "openalex")

    def test_count_only_top_k_zero(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={
            "meta": {"count": 663}, "results": [],
        }) as mocked:
            result = _openalex_search(CTX, filters={"venue": "TIP", "year": 2026}, top_k=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 663)
        self.assertEqual(result.data["items"], [])
        _, params = mocked.call_args[0]
        self.assertIn("publication_year:2026", params["filter"])
        self.assertNotIn("search", params)  # no query -> no full-text search
        self.assertNotIn("sort", params)  # pure filter -> no relevance sort (OpenAlex 400)

    def test_venue_fallback_raw_source_name(self):
        from backend.agent.tools.research.openalex import _compact_work
        work = {"id": "https://openalex.org/W9", "title": "T", "publication_year": 2026, "doi": "https://doi.org/10.1/x",
                "primary_location": {"raw_source_name": "arXiv Preprint"}, "cited_by_count": 1,
                "authorships": [], "abstract_inverted_index": None, "open_access": {"is_oa": True}}
        self.assertEqual(_compact_work(work)["venue"], "arXiv Preprint")

    def test_empty_results(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={
            "meta": {"count": 0}, "results": [],
        }):
            result = _openalex_search(CTX, query="nonexistentxyz")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 0)

    def test_conference_venue_note(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={
            "meta": {"count": 5}, "results": [],
        }) as mocked:
            result = _openalex_search(CTX, filters={"venue": "CVPR", "year": 2026}, top_k=1)
        self.assertTrue(result.ok)
        self.assertIn("venue_note", result.data)
        _, params = mocked.call_args[0]
        # A conference should not produce a source filter; keep only the year
        self.assertEqual(params["filter"], "publication_year:2026")

    def test_api_error(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"_status": 400, "error": "Invalid query parameters"}):
            result = _openalex_search(CTX, query="x")
        self.assertFalse(result.ok)
        self.assertIn("Invalid", result.data["error"])

    def test_network_error(self):
        with mock.patch("backend.agent.tools.research.openalex._get", side_effect=RuntimeError("OpenAlex 网络错误: boom")):
            result = _openalex_search(CTX, query="x")
        self.assertFalse(result.ok)
        self.assertIn("boom", result.data["error"])

    def test_budget_exhausted(self):
        result = _openalex_search({"openalex_budget": {"used": 8, "max": 8}}, query="x")
        self.assertFalse(result.ok)
        self.assertIn("上限", result.data["error"])

    def test_budget_counts(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"meta": {"count": 0}, "results": []}):
            ctx = {}
            _openalex_search(ctx, query="x")
            _openalex_search(ctx, query="y")
        self.assertEqual(ctx["openalex_budget"]["used"], 2)


class GetWorkTests(_WithFreshCtx):
    def test_get_by_doi(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value=_fake_work(doi="10.1109/tip.2026.1")) as mocked:
            result = _openalex_get_work(CTX, doi="10.1109/tip.2026.1")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["matched"])
        self.assertEqual(result.data["title"], "Attention Is All You Need")
        call_url = mocked.call_args[0][0]
        self.assertIn("/works/doi:10.1109/tip.2026.1", call_url)

    def test_get_by_id(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value=_fake_work()) as mocked:
            result = _openalex_get_work(CTX, openalex_id="W7126063349")
        self.assertTrue(result.ok)
        call_url = mocked.call_args[0][0]
        self.assertIn("/works/W7126063349", call_url)

    def test_404_matched_false(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"_status": 404}):
            result = _openalex_get_work(CTX, doi="10.9999/nonexistent")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["matched"])

    def test_missing_param(self):
        result = _openalex_get_work(CTX)
        self.assertFalse(result.ok)


class GetAuthorTests(_WithFreshCtx):
    def _fake_author(self, aid="A5023888391", name="Jason Priem"):
        return {
            "id": f"https://openalex.org/{aid}",
            "display_name": name,
            "orcid": "https://orcid.org/0000-0002-1234-5678",
            "works_count": 120,
            "cited_by_count": 4500,
            "summary_stats": {"h_index": 25},
            "last_known_institutions": [{"display_name": "OurResearch"}],
            "topics": [{"display_name": "Machine Learning"}, {"display_name": "AI"}, {"display_name": "NLP"}],
            "counts_by_year": [{"year": 2024, "works_count": 8, "cited_by_count": 300},
                               {"year": 2025, "works_count": 10, "cited_by_count": 400}],
            "works_api_url": "https://api.openalex.org/works?filter=author.id:A5023888391",
        }

    def test_by_id(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value=self._fake_author()) as mocked:
            result = _openalex_get_author(CTX, openalex_id="A5023888391")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["h_index"], 25)
        self.assertEqual(result.data["works_count"], 120)
        self.assertIn("Machine Learning", result.data["topics"])
        call_url = mocked.call_args[0][0]
        self.assertIn("/authors/A5023888391", call_url)

    def test_by_name_search(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"results": [self._fake_author()]}):
            result = _openalex_get_author(CTX, name="Jason Priem")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["name"], "Jason Priem")

    def test_by_name_no_result(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"results": []}):
            result = _openalex_get_author(CTX, name="Nobody Special")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["matched"])

    def test_404(self):
        with mock.patch("backend.agent.tools.research.openalex._get", return_value={"_status": 404}):
            result = _openalex_get_author(CTX, openalex_id="A9999999999")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["matched"])

    def test_missing_param(self):
        result = _openalex_get_author(CTX)
        self.assertFalse(result.ok)


class RetryTests(unittest.TestCase):
    def test_429_backoff_then_success(self):
        class FakeResp:
            def __init__(self, code, payload=None):
                self.status_code = code
                self._payload = payload or {}
                self.text = "rate limited"

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        calls = {"n": 0}

        def fake_get(url, params=None, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] <= 2:
                return FakeResp(429)
            return FakeResp(200, {"meta": {"count": 1}, "results": [_fake_work()]})

        from backend.agent.tools.research.openalex import _get

        with mock.patch("backend.agent.tools.research.openalex.httpx.get", side_effect=fake_get):
            data = _get("https://api.openalex.org/works", {"per-page": "1"})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(data["meta"]["count"], 1)


class RegistryTests(unittest.TestCase):
    def test_tools_registered_with_category(self):
        tools = openalex_tools()
        self.assertEqual([t.name for t in tools],
                         ["openalex_search", "openalex_get_work", "openalex_get_author"])
        self.assertTrue(all(t.category == "research_api" for t in tools))

    def test_loop_build_tools_includes_openalex(self):
        from backend.agent.tools import build_tools
        names = [t.name for t in build_tools()]
        self.assertIn("openalex_search", names)
        self.assertIn("openalex_get_work", names)
        self.assertIn("openalex_get_author", names)


if __name__ == "__main__":
    unittest.main()
