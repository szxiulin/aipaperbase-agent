"""Metadata corrections must not silently invalidate existing entity references."""
import sqlite3
import unittest
from unittest.mock import patch
from backend.catalog.database import initialize
from backend.catalog.entities import build_entities


class EntityIdentityGuardTests(unittest.TestCase):
    def build(self, prior, identifiers=None, alias_snapshot=None, publication_snapshot=None):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        initialize(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        columns = [row[1] for row in connection.execute("PRAGMA table_info(paper_records)")]
        for rid, arxiv in (identifiers or [("a", "2601.12345"), ("b", "2601.12345")]):
            values = {key: "" for key in columns}
            values.update(record_id=rid, paper_id=rid, title="Shared test paper", normalized_title="sharedtestpaper",
                          authors="Alice Smith", venue="TMLR", year=2026, arxiv_id=arxiv)
            connection.execute("INSERT INTO paper_records VALUES (" + ",".join("?" for _ in columns) + ")",
                               [values[key] for key in columns])
        with patch("backend.catalog.entities._previous_memberships", return_value=prior):
            result = build_entities(connection, "release", "date", alias_snapshot=alias_snapshot,
                                    publication_snapshot=publication_snapshot)
        return connection, result

    def reviewed(self, **changes):
        from unittest.mock import Mock
        import json
        snapshot = Mock()
        snapshot.exists.return_value = True
        row = dict(status="verified_journal_presentation", source_record_id="b",
                   publication_record_id="a", publication_venue="TMLR", publication_year=2026)
        row.update(changes)
        snapshot.read_text.return_value = json.dumps([row])
        return snapshot

    def test_reviewed_presentation_preserves_id_and_selects_journal_without_shared_identifiers(self):
        connection, result = self.build({"a": "kept", "b": "kept"},
            [("a", ""), ("b", "")], publication_snapshot=self.reviewed())
        try:
            self.assertEqual(result["entities"], 1)
            self.assertEqual(tuple(connection.execute(
                "SELECT entity_id, canonical_record_id FROM paper_entities").fetchone()), ("kept", "a"))
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM entity_memberships WHERE entity_id='kept'").fetchone()[0], 2)
        finally:
            connection.close()

    def test_review_cannot_replace_required_alias(self):
        with self.assertRaisesRegex(ValueError, "legacy ID alias"):
            self.build({"a": "old-a", "b": "old-b"}, publication_snapshot=self.reviewed())

    def test_stale_or_unverified_publication_mapping_fails(self):
        for change in (dict(publication_year=2025), dict(publication_venue="JMLR"),
                       dict(publication_record_id="missing"), dict(status="pending_publication_verification")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.build({}, publication_snapshot=self.reviewed(**change))

    def test_existing_distinct_ids_become_review_candidates(self):
        connection, result = self.build({"a": "old-a", "b": "old-b"})
        try:
            self.assertEqual(result["entities"], 2)
            self.assertEqual(dict(connection.execute("SELECT record_id,entity_id FROM entity_memberships")), {"a": "old-a", "b": "old-b"})
            methods = [r[0] for r in connection.execute("SELECT candidate_method FROM entity_review_candidates")]
            self.assertIn("arxiv_identity_review", methods)
        finally:
            connection.close()

    def test_same_existing_entity_and_new_records_still_merge(self):
        for prior in ({}, {"a": "old-a"}, {"a": "old-a", "b": "old-a"}):
            connection, result = self.build(prior)
            try:
                self.assertEqual(result["entities"], 1)
                if prior:
                    self.assertEqual(connection.execute("SELECT entity_id FROM paper_entities").fetchone()[0], "old-a")
            finally:
                connection.close()

    def test_new_record_cannot_bridge_two_existing_entities(self):
        connection, result = self.build({"a": "old-a", "c": "old-c"},
                                        [("a", "2601.12345"), ("b", "2601.12345"), ("c", "2601.12345")])
        try:
            self.assertEqual(result["entities"], 2)
            mapping = dict(connection.execute("SELECT record_id,entity_id FROM entity_memberships"))
            self.assertEqual(mapping["a"], "old-a")
            self.assertEqual(mapping["c"], "old-c")
        finally:
            connection.close()
