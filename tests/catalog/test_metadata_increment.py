import unittest
from datetime import date
from scripts.collect.metadata_increment import registry_matches, fill_missing

class IncrementTests(unittest.TestCase):
    def test_wrong_journal_or_future_date_rejected(self):
        candidate={'year':2026,'title':'A paper','doi':'10.1/a'}
        metadata={'published':{'date-parts':[[2026,10,20]]},'author':[{'family':'Chen'}],
                  'type':'journal-article','DOI':'10.1/A','title':['A paper'],'container-title':['Journal']}
        self.assertTrue(registry_matches(candidate,metadata,'Journal',date(2026,10,21)))
        self.assertFalse(registry_matches(candidate,metadata,'Different',date(2026,10,21)))
        metadata['published']['date-parts']=[[2026,11,1]]
        self.assertFalse(registry_matches(candidate,metadata,'Journal',date(2026,10,21)))

    def test_author_evidence_preserves_identifiers_and_existing_abstract(self):
        row={'paper_id':'program-1','title':'A paper','authors':'','abstract':'existing','doi':''}
        evidence={'title':'A paper','authors':['Jane Chen','Joe Wu'],'abstract':'replacement','source_url':'https://example.org/paper'}
        result=fill_missing(row,evidence,'Jane Chen','Registered preprint')
        self.assertEqual(result,{**row,'authors':'Jane Chen; Joe Wu'})
        self.assertEqual(row['authors'],'')
        with self.assertRaises(ValueError):fill_missing(row,evidence,'Another Person','Source')

    def test_collapsed_official_header_preserves_later_authors(self):
        from scripts.collect.metadata_increment import verify_visible_authors
        full='A One; B Two; C Three; D Four; E Five; F Six; G Seven'
        self.assertEqual(verify_visible_authors(full,full.split('; ')[:6]),'header_prefix_only')
        with self.assertRaises(ValueError):verify_visible_authors(full,['Different Author'])

    def test_verified_early_access_keeps_exact_date_and_unknowns_blank(self):
        from backend.catalog.publication import review
        r=review('TIP',2026,'10.1109/tip.2026.3743079')
        self.assertEqual(r['status'],'verified_early_access')
        self.assertEqual(r['publication_date'],'2026-10-19')
        r=review('TVCG',2026,'10.1109/tvcg.2026.3744785')
        self.assertEqual(r['publication_date'],'2026-10-19')
        self.assertEqual(r['publication_status'],'early_access')
        self.assertFalse(r['publisher_export']['whole_first_online_year_verified'])
        r=review('TPAMI',2026,'10.1109/tpami.2026.3744812')
        self.assertEqual(r['publication_date'],'')
        self.assertEqual(r['publication_status'],'')
