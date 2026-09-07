from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from backend.collections import database, organization, service
from backend.agent.tools.organization import organization_tools


def catalog() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE paper_entities (entity_id TEXT PRIMARY KEY, canonical_record_id TEXT, canonical_title TEXT);
    CREATE TABLE paper_records (record_id TEXT PRIMARY KEY, abstract TEXT);
    CREATE TABLE entity_topic_assignments (entity_id TEXT, topic_id TEXT);
    CREATE TABLE entity_tech_tags (entity_id TEXT, tag TEXT);
    CREATE TABLE entity_aliases (alias_entity_id TEXT PRIMARY KEY, entity_id TEXT);
    """)
    conn.executemany("INSERT INTO paper_entities VALUES (?, ?, ?)", [("p1", "r1", "4KAgent"), ("p2", "r2", "Text Paper")])
    conn.executemany("INSERT INTO paper_records VALUES (?, ?)", [("r1", "An agent for image super-resolution."), ("r2", "Language modelling.")])
    conn.execute("INSERT INTO entity_tech_tags VALUES ('p1', 'agentic')")
    return conn


def reconcile() -> dict:
    return {"items": [
        {"entity_id": "p1", "title": "4KAgent", "index_status": "indexed_current", "actual_chunk_count": 4,
         "actual_parsed_sha256": "h1", "pipeline_fingerprint": "f1"},
        {"entity_id": "p2", "title": "Text Paper", "index_status": "index_stale", "actual_chunk_count": 9,
         "actual_parsed_sha256": "h2", "pipeline_fingerprint": "f1"},
    ]}


class OrganizationTest(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog()
        self.user = sqlite3.connect(":memory:")
        self.user.row_factory = sqlite3.Row
        database.initialize(self.user)
        self.agent = service.create_collection(self.user, name="Agent", description="核心 Agent 方法", catalog_release_id="r")
        self.image = service.create_collection(self.user, name="图像", description="图像任务", catalog_release_id="r")
        self.image_agent = service.create_collection(self.user, name="图像Agent", description="Agent 直接用于图像", catalog_release_id="r")

    def tearDown(self): self.catalog.close(); self.user.close()

    def test_only_current_papers_and_image_agent_needs_evidence(self):
        plan = organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.agent['collection_id'], self.image['collection_id'], self.image_agent['collection_id']], entity_ids=['p1', 'p2'])
        self.assertEqual({item['entity_id'] for item in plan['suggestions']}, {'p1', 'p2'})
        self.assertTrue(all(item['decision'] == 'review' for item in plan['suggestions'] if item['entity_id'] == 'p2'))
        image_agent = next(item for item in plan['suggestions'] if item['collection_id'] == self.image_agent['collection_id'])
        self.assertEqual(image_agent['decision'], 'review')
        self.assertFalse(image_agent['evidence'])
        self.assertEqual(service.collection_entity_ids(self.user, self.agent['collection_id']), [])

    def test_apply_is_whitelisted_idempotent_and_expires_on_state_change(self):
        plan = organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.agent['collection_id']], entity_ids=['p1'])
        suggestion = plan['suggestions'][0]
        result = organization.apply(self.user, self.catalog, reconcile(), run_id=plan['organization_run_id'], selected=[suggestion])
        self.assertEqual(result['added'], 1)
        self.assertEqual(service.collection_entity_ids(self.user, self.agent['collection_id']), ['p1'])
        self.assertEqual(organization.apply(self.user, self.catalog, reconcile(), run_id=plan['organization_run_id'], selected=[suggestion])['added'], 0)
        plan2 = organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.image['collection_id']], entity_ids=['p1'])
        changed = reconcile(); changed['items'][0]['actual_chunk_count'] = 5
        with self.assertRaisesRegex(ValueError, '过期'):
            organization.apply(self.user, self.catalog, changed, run_id=plan2['organization_run_id'], selected=[plan2['suggestions'][0]])
        self.assertEqual(organization.get_run(self.user, plan2['organization_run_id'])['status'], 'expired')

    def test_untrusted_or_excluded_pairs_cannot_be_applied(self):
        plan = organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.agent['collection_id']], entity_ids=['p1'])
        with self.assertRaisesRegex(ValueError, '不属于'):
            organization.apply(self.user, self.catalog, reconcile(), run_id=plan['organization_run_id'],
                               selected=[{'entity_id': 'malicious', 'collection_id': self.agent['collection_id']}])

    def test_scope_is_never_silently_expanded(self):
        with self.assertRaisesRegex(ValueError, '明确指定'):
            organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.agent['collection_id']])
        plan = organization.propose(self.user, self.catalog, reconcile(), collection_ids=[self.agent['collection_id']], entity_ids=['p2'])
        self.assertEqual([item['entity_id'] for item in plan['suggestions']], ['p2'])
        self.assertEqual(plan['suggestions'][0]['decision'], 'review')

    def test_chat_draft_uses_scoped_write_connection_not_read_collection_connection(self):
        tool = next(item for item in organization_tools() if item.name == 'propose_collection_assignments')
        with mock.patch("backend.agent.tools.organization._reconcile", return_value=reconcile()):
            result = tool.handler({"catalog_conn": self.catalog, "collections_conn": None,
                                   "organization_draft_conn": self.user, "conversation_id": "chat_1"},
                                  [self.agent['collection_id']], ['p1'], False, None)
        self.assertTrue(result.ok)
        self.assertTrue(result.data['organization_run_id'])
        self.assertEqual(service.collection_entity_ids(self.user, self.agent['collection_id']), [])
