import unittest,json,gzip,csv
from pathlib import Path
from scripts.collect.publication_baseline import eligible_title,publication_identity
ROOT=Path(__file__).resolve().parents[2]
class PublicationBaselineTests(unittest.TestCase):
    def test_research_named_preface_is_kept(self):
        self.assertTrue(eligible_title('PrefAce: Face-Centric Pretraining with Self-Structure Aware Distillation'))
        self.assertTrue(eligible_title('Preface: A Data-driven Volumetric Prior for Few-shot Ultra High-resolution Face Synthesis'))
        self.assertFalse(eligible_title('Program Chairs’ Report on Peer Review at ACL 2023'))

    def test_source_ids_are_scoped_by_venue_and_year(self):
        base={'venue':'ICML','year':2025,'paper_id':'12','paper_url':'','doi':''}
        self.assertNotEqual(publication_identity(base),publication_identity({**base,'venue':'IJCAI'}))
        self.assertNotEqual(publication_identity(base),publication_identity({**base,'year':2026}))

    def test_formal_inventory_does_not_promote_accepted_lists_or_unknown_to_zero(self):
        r=json.loads((ROOT/'data/catalog/reports/formal_publication_20261021.json').read_text())
        self.assertEqual(len(r['rows']),120)
        self.assertEqual(len({x['venue'] for x in r['rows']}),30)
        for x in r['rows']:
            if x['status']=='unverified':self.assertIsNone(x['reference_count'])
            if x['venue']=='COLM' and x['year']==2026:self.assertEqual(x['status'],'unverified')
        p=ROOT/'data/catalog/reports/publication_items_20261021.csv.gz'
        with gzip.open(p,'rt',encoding='utf-8-sig') as f:
            count=0;seen=set()
            for x in csv.DictReader(f):
                self.assertNotIn(x['publication_key'],seen);seen.add(x['publication_key']);count+=1
                self.assertIn(x['evidence_kind'],['official_proceedings','official_published_journal_index','individual_official_page'])
                if x['identity_status']=='matched':
                    self.assertEqual(len(x['matched_record_ids'].split(' | ')),1)
                if x['publication_date']:self.assertLessEqual(x['publication_date'],'2026-10-21')
            self.assertEqual(count,r['publication_items'])
