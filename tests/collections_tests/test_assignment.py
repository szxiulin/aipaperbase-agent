import sqlite3
import unittest
from unittest import mock
from tests.collections_tests import test_organization as base
reconcile = base.reconcile
from backend.collections import organization, service
from backend.agent.tools.organization import organization_tools
from backend.agent.tools.base import ToolRegistry
from raglib import Chunk, InMemoryStore
from types import SimpleNamespace


class AssignmentTest(unittest.TestCase):
    tearDown = base.OrganizationTest.tearDown
    def setUp(self):
        base.OrganizationTest.setUp(self)
        self.catalog.execute("CREATE TABLE data_releases (release_id TEXT,status TEXT,created_at TEXT)")
        self.catalog.execute("INSERT INTO data_releases VALUES ('r','ready','now')")
        self.catalog.execute("INSERT INTO entity_aliases VALUES ('alias','p2')")

    def plan(self, **kwargs):
        return organization.propose(self.user, self.catalog, reconcile(), operation='assign',
                                    collection_ids=[], target_name='CV Agent', **kwargs)

    def test_new_target_and_stale_alias_are_atomic_and_idempotent(self):
        plan = self.plan(entity_ids=['alias', 'p2'])
        self.assertEqual(plan['scope'], ['p2'])
        self.assertEqual(len(service.list_collections(self.user)), 3)
        selected = plan['suggestions']
        result = organization.apply(self.user,self.catalog,{},run_id=plan['run_id'],selected=selected)
        self.assertEqual(result['added'], 1)
        repeat = organization.apply(self.user,self.catalog,{},run_id=plan['run_id'],selected=selected)
        self.assertEqual(repeat['result'], organization.get_run(self.user,plan['run_id'])['result'])
        self.assertEqual(repeat['added'], 0)

    def test_transaction_failure_rolls_back_collection_members_and_run(self):
        plan = self.plan(entity_ids=['p1','p2'])
        self.user.executescript("CREATE TRIGGER reject_member BEFORE INSERT ON collection_members WHEN NEW.entity_id='p2' BEGIN SELECT RAISE(ABORT,'fixture failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            organization.apply(self.user,self.catalog,{},run_id=plan['run_id'],selected=plan['suggestions'])
        self.assertEqual(len(service.list_collections(self.user)), 3)
        self.assertEqual(organization.get_run(self.user,plan['run_id'])['status'],'draft')
        self.assertEqual(self.user.execute('SELECT COUNT(*) FROM collection_members').fetchone()[0],0)

    def test_no_scope_expansion_and_limit(self):
        with self.assertRaises(ValueError): self.plan()
        plan = self.plan(entity_ids=[] , allow_all=True)
        self.assertEqual(plan['scope'], [])
        many = {'items': [{'entity_id':str(i)} for i in range(101)]}
        with self.assertRaises(ValueError):
            organization.propose(self.user,self.catalog,many,operation='assign',collection_ids=[],target_name='x',allow_all=True)

    def test_model_rule_and_real_evidence_determine_decision(self):
        store = InMemoryStore()
        store.upsert([Chunk(id='c1',document_id='p1',text='Actual content: image planning agent.',metadata={})], [[1.,0.]])
        cid = self.agent['collection_id']
        ctx = {'catalog_conn':self.catalog,'organization_draft_conn':self.user,'pipeline':SimpleNamespace(store=store)}
        decision = {'entity_id':'p1','collection_id':cid,'rule':'核心 Agent 方法','decision':'exclude',
                    'reason':'模型根据规则判断本文只是对照实验。','confidence':0.9,
                    'evidence':[{'entity_id':'p1','chunk_id':'c1','text':'forged'}]}
        with mock.patch('backend.agent.tools.organization._reconcile',return_value=reconcile()):
            result = ToolRegistry(organization_tools()).dispatch('propose_collection_assignments',
                    {'collection_ids':[cid],'entity_ids':['p1'],'decisions':[decision]},ctx)
        self.assertTrue(result.ok, result.data)
        item = result.data['suggestions'][0]
        self.assertEqual(item['decision'],'exclude')
        self.assertIn('Actual content',item['evidence'][0]['text_preview'])
        decision['rule']='wrong rule'
        with mock.patch('backend.agent.tools.organization._reconcile',return_value=reconcile()):
            result = ToolRegistry(organization_tools()).dispatch('propose_collection_assignments',
                    {'collection_ids':[cid],'entity_ids':['p1'],'decisions':[decision]},ctx)
        self.assertEqual(result.data['suggestions'][0]['decision'],'review')

    def test_chat_tool_refuses_all_for_ambiguous_pronoun(self):
        result = ToolRegistry(organization_tools()).dispatch('propose_collection_membership',
            {'target_name':'CV Agent','allow_all':True},
            {'query':'把这几篇加入 CV Agent','catalog_conn':self.catalog,'organization_draft_conn':self.user})
        self.assertFalse(result.ok)
        self.assertIn('明确范围',result.data['error'])

    def test_missing_model_configuration_does_not_request_network(self):
        from backend.agent.llm import chat
        client = mock.Mock()
        with self.assertRaisesRegex(RuntimeError,'未配置'):
            chat(client,base_url='',api_key='',model='',messages=[],tools=[],thinking='disabled',reasoning_effort='low',max_tokens=10)
        client.post.assert_not_called()
