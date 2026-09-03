"""Unit tests for arXiv tools: Atom parsing / 3 atomic tools / three ingest states / registration.

Covers:
- _parse_feed: Atom namespace parsing, version-suffix stripping, pdf link matching, totalResults
- arxiv_search: query required, parameter construction (category/year/sort/top_k clamp)
- arxiv_get_paper: found / not found / missing params
- arxiv_ingest: full=False ingests into catalog (mocked network layer)
- arxiv_ingest.ingest_entry: new / exists / merged three states + venue='arXiv' + FTS increment
- arxiv_tools(): 3 tools registered, category, schema required
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date
from unittest import mock

from backend.agent.tools.research import arxiv as tools_mod
from backend.agent.tools.base import ToolResult
from backend.catalog import arxiv_ingest, database, search

CTX: dict = {}

ENTRY = {
    "arxiv_id": "2401.00001",
    "title": "Attention Is All You Need",
    "authors": ["Vaswani", "Shazeer"],
    "abstract": "The dominant sequence transduction models are based on complex recurrent or "
                "convolutional neural networks in an encoder-decoder configuration.",
    "published": "2017-06-12",
    "year": 2017,
    "doi": "10.48550/arXiv.1706.03762",
    "pdf_url": "https://arxiv.org/pdf/2401.00001",
    "paper_url": "https://arxiv.org/abs/2401.00001",
    "category": "cs.CL",
}

FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>  Test Paper Title  </title>
    <summary>line one
line two summary</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice</name></author>
    <author><name>  Bob  </name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2401.00001"/>
    <link title="alternate" href="https://arxiv.org/abs/2401.00001"/>
    <arxiv:primary_category term="cs.CV"/>
    <arxiv:doi>10.1234/example</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002</id>
    <title>Second Paper</title>
    <summary>another abstract</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Carol</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2401.00002"/>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>"""


class _FreshCtxMixin(unittest.TestCase):
    def setUp(self) -> None:
        global CTX
        CTX = {}


class ParseFeedTests(unittest.TestCase):
    def test_atom_parsing(self) -> None:
        feed = tools_mod._parse_feed(FEED_XML)
        self.assertEqual(feed["total"], 2)
        first = feed["entries"][0]
        self.assertEqual(first["arxiv_id"], "2401.00001")  # v2 version suffix stripped
        self.assertEqual(first["title"], "Test Paper Title")  # whitespace cleaned
        self.assertEqual(first["authors"], ["Alice", "Bob"])
        self.assertEqual(first["year"], 2024)
        self.assertEqual(first["doi"], "10.1234/example")
        self.assertEqual(first["pdf_url"], "https://arxiv.org/pdf/2401.00001")
        self.assertEqual(first["paper_url"], "https://arxiv.org/abs/2401.00001")
        self.assertEqual(first["category"], "cs.CV")

    def test_abstract_single_line(self) -> None:
        feed = tools_mod._parse_feed(FEED_XML)
        self.assertNotIn("\n", feed["entries"][0]["abstract"])
        self.assertEqual(feed["entries"][0]["abstract"], "line one line two summary")

    def test_pdf_url_fallback(self) -> None:
        # When there is no pdf link, derive it from the arxiv_id
        payload = FEED_XML.replace(b'<link title="pdf" href="https://arxiv.org/pdf/2401.00001"/>', b"")
        feed = tools_mod._parse_feed(payload)
        self.assertEqual(feed["entries"][0]["pdf_url"], "https://arxiv.org/pdf/2401.00001")


class CompactTests(unittest.TestCase):
    def test_abstract_truncated(self) -> None:
        entry = dict(ENTRY, abstract="x" * 500)
        out = tools_mod._compact(entry)
        self.assertEqual(len(out["abstract"]), 400)

    def test_authors_capped(self) -> None:
        entry = dict(ENTRY, authors=[f"a{i}" for i in range(10)])
        out = tools_mod._compact(entry)
        self.assertEqual(len(out["authors"]), 5)


class SearchToolTests(_FreshCtxMixin):
    def test_query_required(self) -> None:
        result = tools_mod._arxiv_search(CTX, query="   ")
        self.assertFalse(result.ok)
        self.assertIn("必填", result.data["error"])

    def test_search_params_and_provenance(self) -> None:
        with mock.patch.object(tools_mod, "_fetch", return_value=FEED_XML) as mocked:
            result = tools_mod._arxiv_search(CTX, query="diffusion model", category="cs.CV",
                                             year=2024, sort="date", top_k=20)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 2)
        args = mocked.call_args[1] or {}
        params = mocked.call_args.args[0] if mocked.call_args.args else args
        self.assertIn('all:"diffusion model"', params["search_query"])
        self.assertIn("AND cat:cs.CV", params["search_query"])
        self.assertIn("submittedDate:[202401010000 TO 202412312359]", params["search_query"])
        self.assertEqual(params["sortBy"], "submittedDate")  # sort=date
        self.assertEqual(params["max_results"], 20)
        self.assertIsInstance(result.provenance, list)
        self.assertEqual(result.provenance[0]["source"], "arxiv")

    def test_top_k_clamped(self) -> None:
        with mock.patch.object(tools_mod, "_fetch", return_value=FEED_XML) as mocked:
            tools_mod._arxiv_search(CTX, query="agent", top_k=999)
        params = mocked.call_args.args[0]
        self.assertEqual(params["max_results"], 20)

    def test_fetch_failure(self) -> None:
        with mock.patch.object(tools_mod, "_fetch", side_effect=RuntimeError("boom")):
            result = tools_mod._arxiv_search(CTX, query="agent")
        self.assertFalse(result.ok)
        self.assertIn("请求失败", result.data["error"])


class GetPaperToolTests(_FreshCtxMixin):
    def test_arxiv_id_required(self) -> None:
        result = tools_mod._arxiv_get_paper(CTX, arxiv_id=" ")
        self.assertFalse(result.ok)
        self.assertIn("arxiv_id 必填", result.data["error"])

    def test_found(self) -> None:
        with mock.patch.object(tools_mod, "_fetch", return_value=FEED_XML):
            result = tools_mod._arxiv_get_paper(CTX, arxiv_id="2401.00001v2")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["arxiv_id"], "2401.00001")
        self.assertEqual(result.data["title"], "Test Paper Title")
        self.assertEqual(len(result.provenance), 1)

    def test_not_found(self) -> None:
        empty = FEED_XML.replace(b"<opensearch:totalResults>2</opensearch:totalResults>",
                                 b"<opensearch:totalResults>0</opensearch:totalResults>")
        empty = empty.split(b"<entry>")[0] + b"</feed>"
        with mock.patch.object(tools_mod, "_fetch", return_value=empty):
            result = tools_mod._arxiv_get_paper(CTX, arxiv_id="9999.99999")
        self.assertFalse(result.ok)
        self.assertIn("未找到", result.data["error"])


def _make_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.initialize(conn)
    return conn


def _insert_cvpr_record(conn: sqlite3.Connection, *, record_id="r-cvpr-1", paper_id="cvpr1234",
                        title="Attention Is All You Need", year=2017, release_id="catalog-test") -> None:
    """Insert a conference record (for matching in the merged scenario)."""
    ntitle = arxiv_ingest.normalize_title(title)
    conn.execute(
        "INSERT INTO data_releases (release_id, created_at, source_digest,"
        " source_file_count, record_count, status)"
        " VALUES (?, '2024-01-01T00:00:00+00:00', 'digest', 1, 1, 'ready')",
        (release_id,),
    )
    conn.execute(
        "INSERT INTO source_files (source_file, release_id, sha256, row_count,"
        " venue, venue_type, year, list_status)"
        " VALUES ('data/cvpr/2024.csv', ?, 'sha', 1, 'CVPR', 'conference', ?, 'active')",
        (release_id, year),
    )
    conn.execute(
        "INSERT INTO paper_records (record_id, paper_id, venue, venue_type, year,"
        " track, title, normalized_title, authors, abstract, abstract_source_name,"
        " abstract_source_url, abstract_source_tier, abstract_fetched_at, doi, arxiv_id,"
        " paper_url, pdf_url, source_name, source_url, source_tier, verification_status,"
        " list_status, fetched_at, source_file, imported_at, release_id)"
        " VALUES (?, ?, 'CVPR', 'conference', ?, '', ?, ?, 'authors', 'abs', 'OpenReview',"
        " 'https://openreview.net', 'official', '2024-01-01T00:00:00+00:00', '', '',"
        " 'https://cvpr.com', 'https://cvpr.com', 'CVPR', 'https://cvpr.com', 'official',"
        " 'verified', 'active', '2024-01-01T00:00:00+00:00', ?, '2024-01-01T00:00:00+00:00', ?)",
        (record_id, paper_id, year, title, ntitle, "data/cvpr/2024.csv", release_id),
    )
    conn.execute(
        "INSERT INTO paper_entities (entity_id, canonical_record_id, canonical_title,"
        " first_year, last_year, record_count, venue_count, entity_status, created_at, release_id)"
        " VALUES (?, ?, ?, ?, ?, 1, 1, 'single', '2024-01-01T00:00:00+00:00', ?)",
        ("ent-cvpr", record_id, title, year, year, release_id),
    )
    conn.execute(
        "INSERT INTO entity_memberships (record_id, entity_id, match_method, evidence_value,"
        " confidence, decision_status, is_canonical, created_at, release_id)"
        " VALUES (?, ?, 'singleton', '', 'single', 'auto_accepted', 1,"
        " '2024-01-01T00:00:00+00:00', ?)",
        (record_id, "ent-cvpr", release_id),
    )
    conn.commit()


class IngestEntryTests(unittest.TestCase):
    """arxiv_ingest.ingest_entry three states: new / exists / merged."""

    def setUp(self) -> None:
        self.conn = _make_memory_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_new_creates_entity(self) -> None:
        result = arxiv_ingest.ingest_entry(self.conn, ENTRY, day=date(2026, 8, 30))
        self.conn.commit()
        self.assertEqual(result["status"], "new")
        # venue='arXiv' is on par with conference/journal
        row = self.conn.execute(
            "SELECT venue, venue_type, list_status, title, paper_id FROM paper_records"
            " WHERE record_id = ?", (result["record_id"],)
        ).fetchone()
        self.assertEqual(row["venue"], "arXiv")
        self.assertEqual(row["venue_type"], "preprint")
        self.assertEqual(row["list_status"], "rolling")
        self.assertEqual(row["paper_id"], "2401.00001")
        # entity
        ent = self.conn.execute(
            "SELECT record_count, venue_count, entity_status FROM paper_entities"
            " WHERE entity_id = ?", (result["entity_id"],)
        ).fetchone()
        self.assertEqual(ent["record_count"], 1)
        self.assertEqual(ent["entity_status"], "single")
        # membership canonical
        mem = self.conn.execute(
            "SELECT match_method, is_canonical FROM entity_memberships WHERE record_id = ?",
            (result["record_id"],),
        ).fetchone()
        self.assertEqual(mem["match_method"], "arxiv_id")
        self.assertEqual(mem["is_canonical"], 1)
        # release + source_files aggregate rows
        rel = self.conn.execute(
            "SELECT release_id FROM data_releases WHERE release_id = 'arxiv-20260830'"
        ).fetchone()
        self.assertIsNotNone(rel)
        sf = self.conn.execute(
            "SELECT venue, venue_type FROM source_files WHERE source_file = 'arxiv:20260830.csv'"
        ).fetchone()
        self.assertEqual(sf["venue"], "arXiv")
        # FTS increment: retrieval can match
        self.assertTrue(search.has_index(self.conn))
        self.assertIn(result["record_id"], search.resolve(self.conn, "attention"))

    def test_exists_idempotent(self) -> None:
        first = arxiv_ingest.ingest_entry(self.conn, ENTRY)
        self.conn.commit()
        second = arxiv_ingest.ingest_entry(self.conn, ENTRY)
        self.conn.commit()
        self.assertEqual(second["status"], "exists")
        self.assertEqual(second["entity_id"], first["entity_id"])
        count = self.conn.execute("SELECT COUNT(*) FROM paper_records").fetchone()[0]
        self.assertEqual(count, 1)

    def test_merged_into_existing_entity(self) -> None:
        """arXiv first, later accepted: title match -> join the existing entity, no new entity."""
        _insert_cvpr_record(self.conn)
        result = arxiv_ingest.ingest_entry(self.conn, ENTRY)
        self.conn.commit()
        self.assertEqual(result["status"], "merged")
        self.assertEqual(result["entity_id"], "ent-cvpr")
        self.assertIn("Attention Is All You Need", result["matched_title"])
        # entity stats updated
        ent = self.conn.execute(
            "SELECT record_count, venue_count, entity_status FROM paper_entities"
            " WHERE entity_id = 'ent-cvpr'"
        ).fetchone()
        self.assertEqual(ent["record_count"], 2)
        self.assertEqual(ent["venue_count"], 2)  # CVPR + arXiv
        self.assertEqual(ent["entity_status"], "merged")
        # membership: the arXiv record hangs under the existing entity, not canonical
        mem = self.conn.execute(
            "SELECT match_method, is_canonical FROM entity_memberships WHERE record_id = ?",
            (result["record_id"],),
        ).fetchone()
        self.assertEqual(mem["match_method"], "arxiv_title")
        self.assertEqual(mem["is_canonical"], 0)
        # No new entity: the total entity count is still 1
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM paper_entities").fetchone()[0], 1
        )

    def test_arxiv_id_required(self) -> None:
        with self.assertRaises(ValueError):
            arxiv_ingest.ingest_entry(self.conn, {"title": "x"})


class IngestToolTests(_FreshCtxMixin):
    """arxiv_ingest tool (network layer mocked; full=False only ingests metadata)."""

    def test_full_false_metadata_only(self) -> None:
        conn = _make_memory_db()
        with mock.patch.object(tools_mod, "_fetch_by_id", return_value=[ENTRY]), \
             mock.patch("backend.catalog.database.connect", return_value=conn):
            result = tools_mod._arxiv_ingest(CTX, arxiv_id="2401.00001", full=False)
        conn.close()
        self.assertIsInstance(result, ToolResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "new")
        self.assertEqual(result.data["venue"], "arXiv")
        self.assertNotIn("stages", result.data)  # did not go through the download/parse pipeline

    def test_ingest_missing_id(self) -> None:
        result = tools_mod._arxiv_ingest(CTX, arxiv_id="  ")
        self.assertFalse(result.ok)
        self.assertIn("arxiv_id 必填", result.data["error"])

    def test_ingest_not_found(self) -> None:
        with mock.patch.object(tools_mod, "_fetch_by_id", return_value=[]):
            result = tools_mod._arxiv_ingest(CTX, arxiv_id="9999.99999")
        self.assertFalse(result.ok)
        self.assertIn("未找到", result.data["error"])

    def test_ingest_catalog_failure_rolls_back(self) -> None:
        conn = _make_memory_db()

        def _boom(connection, entry, *, day=None):
            raise RuntimeError("constraint violation")

        with mock.patch.object(tools_mod, "_fetch_by_id", return_value=[ENTRY]), \
             mock.patch("backend.catalog.database.connect", return_value=conn), \
             mock.patch("backend.catalog.arxiv_ingest.ingest_entry", side_effect=_boom):
            result = tools_mod._arxiv_ingest(CTX, arxiv_id="2401.00001", full=False)
        conn.close()
        self.assertFalse(result.ok)
        self.assertIn("入库失败", result.data["error"])


class RegistrationTests(unittest.TestCase):
    def test_three_tools_registered(self) -> None:
        tools = tools_mod.arxiv_tools()
        names = {t.name for t in tools}
        self.assertEqual(names, {"arxiv_search", "arxiv_get_paper", "arxiv_ingest"})
        for t in tools:
            self.assertEqual(t.category, "research_api")

    def test_schema_required_fields(self) -> None:
        tools = {t.name: t for t in tools_mod.arxiv_tools()}
        self.assertTrue(tools["arxiv_search"].parameters["required"])
        self.assertEqual(tools["arxiv_search"].parameters["required"], ["query"])
        self.assertEqual(tools["arxiv_get_paper"].parameters["required"], ["arxiv_id"])
        self.assertEqual(tools["arxiv_ingest"].parameters["required"], ["arxiv_id"])


if __name__ == "__main__":
    unittest.main()
