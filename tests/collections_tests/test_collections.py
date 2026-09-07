from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from backend.collections import database as collections_db
from backend.collections import export, service


CATALOG = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


def build_catalog_fixture() -> sqlite3.Connection:
    """A minimal in-memory catalog with the columns the collections service reads."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE data_releases (release_id TEXT PRIMARY KEY, created_at TEXT, status TEXT);
        CREATE TABLE paper_entities (
            entity_id TEXT PRIMARY KEY, canonical_record_id TEXT, canonical_title TEXT,
            first_year INTEGER, last_year INTEGER, record_count INTEGER, venue_count INTEGER
        );
        CREATE TABLE paper_records (
            record_id TEXT PRIMARY KEY, title TEXT, authors TEXT, venue TEXT,
            venue_type TEXT, year INTEGER, list_status TEXT, paper_url TEXT, doi TEXT, arxiv_id TEXT
        );
        CREATE TABLE entity_memberships (entity_id TEXT, record_id TEXT);
        CREATE TABLE entity_aliases (alias_entity_id TEXT PRIMARY KEY, entity_id TEXT);
        """
    )
    connection.execute("INSERT INTO data_releases VALUES ('rel-1', '2026-08-29T00:00:00+00:00', 'ready')")
    connection.executemany(
        "INSERT INTO paper_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("r1", "Alpha Paper", "Alice", "CVPR", "conference", 2024, "final", "https://a", "10.1/alpha", "2401.00001"),
            ("r2", "Beta Paper", "Bob", "TPAMI", "journal", 2023, "final", "https://b", "10.1/beta", ""),
            ("r3", "Gamma Paper", "Carol", "ICML", "conference", 2025, "final", "https://c", "10.1/gamma", ""),
        ],
    )
    connection.executemany(
        "INSERT INTO paper_entities VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", "r1", "Alpha Paper", 2024, 2024, 1, 1),
            ("e2", "r2", "Beta Paper", 2023, 2023, 1, 1),
            ("e3", "r3", "Gamma Paper", 2025, 2025, 1, 1),
        ],
    )
    connection.executemany(
        "INSERT INTO entity_memberships VALUES (?, ?)",
        [("e1", "r1"), ("e2", "r2"), ("e3", "r3")],
    )
    connection.execute("INSERT INTO entity_aliases VALUES ('old_e3', 'e3')")
    return connection


@contextmanager
def open_collections_db():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "collections" / "collections.sqlite"
    conn = collections_db.connect(path)
    service.ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


class ResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog_fixture()

    def tearDown(self) -> None:
        self.catalog.close()

    def test_resolve_current_alias_and_missing(self) -> None:
        result = service.resolve_entity_ids(self.catalog, ["e1", "old_e3", "gone"])
        self.assertEqual(result["e1"], {"entity_id": "e1", "status": "current"})
        self.assertEqual(result["old_e3"], {"entity_id": "e3", "status": "alias"})
        self.assertEqual(result["gone"], {"entity_id": "gone", "status": "missing"})


class CollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog_fixture()

    def tearDown(self) -> None:
        self.catalog.close()

    def test_create_add_dedup_export_delete(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(
                user, name="测试集合", catalog_release_id="rel-1", entity_ids=["e1", "e2"]
            )
            self.assertEqual(created["member_count"], 2)

            result = service.add_members(
                user, self.catalog, created["collection_id"], ["e1", "e3"]
            )
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["already_present"], 1)

            detail = service.get_collection(user, self.catalog, created["collection_id"])
            self.assertEqual(detail["counts"], {"current": 3, "alias": 0, "missing": 0})
            self.assertEqual(detail["total"], 3)

            for fmt in ("csv", "json", "bibtex", "markdown"):
                content, content_type, filename = export.export_collection(
                    user, self.catalog, created["collection_id"], fmt
                )
                self.assertTrue(content)
                self.assertTrue(filename)

            removed = service.remove_members(user, created["collection_id"], ["e1"])
            self.assertEqual(removed["removed"], 1)

            service.delete_collection(user, created["collection_id"])
            with self.assertRaises(KeyError):
                service.collection_summary(user, created["collection_id"])

    def test_alias_and_missing_members(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(
                user, name="迁移", catalog_release_id="rel-1", entity_ids=["old_e3", "gone"]
            )
            detail = service.get_collection(user, self.catalog, created["collection_id"])
            self.assertEqual(detail["counts"], {"current": 0, "alias": 1, "missing": 1})
            items = {item["entity_id"]: item for item in detail["items"]}
            self.assertEqual(items["old_e3"]["resolved_entity_id"], "e3")
            self.assertEqual(items["old_e3"]["title"], "Gamma Paper")
            self.assertEqual(items["gone"]["status"], "missing")
            self.assertNotIn("title", items["gone"])

    def test_add_rejects_unknown_entities(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(user, name="校验", catalog_release_id="rel-1")
            with self.assertRaises(ValueError):
                service.add_members(user, self.catalog, created["collection_id"], ["nope"])

    def test_add_members_canonicalizes_alias_and_detects_historical_alias(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(user, name="规范化", catalog_release_id="rel-1")
            result = service.add_members(user, self.catalog, created["collection_id"], ["old_e3", "e3"])
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["already_present"], 0)
            self.assertEqual(service.collection_entity_ids(user, created["collection_id"]), ["e3"])

            # Simulate a pre-Sprint-021 stored alias. It remains untouched but
            # must block a new canonical duplicate.
            user.execute(
                "INSERT INTO collection_members (collection_id, entity_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                (created["collection_id"], "old_e3", "manual", "2026-01-01T00:00:00+00:00"),
            )
            user.execute("DELETE FROM collection_members WHERE collection_id=? AND entity_id='e3'", (created["collection_id"],))
            result = service.add_members(user, self.catalog, created["collection_id"], ["e3"])
            self.assertEqual(result["added"], 0)
            self.assertEqual(result["already_present"], 1)
            self.assertEqual(result["historical_alias_members"][0]["stored_entity_id"], "old_e3")

    def test_same_canonical_can_belong_to_multiple_collections(self) -> None:
        with open_collections_db() as user:
            collections = [service.create_collection(user, name=f"集合{i}", catalog_release_id="rel-1") for i in range(3)]
            for collection in collections:
                self.assertEqual(service.add_members(user, self.catalog, collection["collection_id"], ["old_e3"])["added"], 1)

    def test_export_skips_missing_and_dedupes_alias(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(
                user, name="去重", catalog_release_id="rel-1", entity_ids=["e3", "old_e3", "gone"]
            )
            rows = service.export_rows(user, self.catalog, created["collection_id"])
            self.assertEqual([row["entity_id"] for row in rows], ["e3"])

    def test_export_filename_is_ascii_safe(self) -> None:
        with open_collections_db() as user:
            created = service.create_collection(
                user, name="中文集合名导出", catalog_release_id="rel-1", entity_ids=["e1"]
            )
            extensions = {"csv": ".csv", "json": ".json", "bibtex": ".bib", "markdown": ".md"}
            for fmt, ext in extensions.items():
                _, _, filename = export.export_collection(
                    user, self.catalog, created["collection_id"], fmt
                )
                self.assertTrue(filename.isascii(), filename)
                self.assertTrue(filename.endswith(ext), filename)


class RealCatalogCollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CATALOG.exists():
            raise unittest.SkipTest("catalog.sqlite 尚未构建")

    def test_collection_against_real_catalog(self) -> None:
        catalog = sqlite3.connect(CATALOG)
        catalog.row_factory = sqlite3.Row
        try:
            release = service.current_catalog_release(catalog)
            self.assertTrue(release)
            merged = catalog.execute(
                "SELECT entity_id FROM paper_entities WHERE entity_status = 'merged' ORDER BY entity_id LIMIT 3"
            ).fetchall()
            self.assertTrue(merged)
            ids = [row["entity_id"] for row in merged]

            with open_collections_db() as user:
                created = service.create_collection(
                    user, name="真实数据", catalog_release_id=release, entity_ids=ids
                )
                self.assertEqual(created["member_count"], len(ids))

                detail = service.get_collection(user, catalog, created["collection_id"])
                self.assertEqual(detail["counts"]["current"], len(ids))
                self.assertTrue(all(item["title"] for item in detail["items"]))
                self.assertTrue(all(item["appearances"] for item in detail["items"]))

                for fmt in ("csv", "json", "bibtex", "markdown"):
                    content, _, _ = export.export_collection(
                        user, catalog, created["collection_id"], fmt
                    )
                    self.assertTrue(content)
        finally:
            catalog.close()


if __name__ == "__main__":
    unittest.main()
