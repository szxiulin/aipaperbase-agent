import unittest
from scripts.collect.official_programs_2026 import emnlp_main, acmmm_main

class OfficialProgramTest(unittest.TestCase):
    def test_presenter_is_not_an_author_list_and_findings_excluded(self):
        rows=emnlp_main([{'Paper ID #-track)':'1-MAIN','Title':'Study','Presenters Name':'Speaker'},
                         {'Paper ID #-track)':'2-FIND','Title':'Excluded'}])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['authors'],'')
        self.assertEqual(rows[0]['paper_url'],'')
        self.assertEqual(rows[0]['paper_id'],'program-1-MAIN')
    def test_repeated_oral_and_poster_are_one_paper(self):
        paper={'paper_id':'mfp1','title':'Paper','authors':['A','B'],'abstract':'','track':'Main Track','emails':['private@example.org']}
        rows=acmmm_main({'entries':[{'papers':[paper,dict(paper,track='Demo and Video')]},{'papers':[paper]}]})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['authors'],'A; B')
        self.assertNotIn('emails',rows[0])
    def test_conflicting_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            emnlp_main([{'Paper ID #-track)':'1-MAIN','Title':'First'},
                        {'Paper ID #-track)':'1-MAIN','Title':'Different'}])

class SpringerEvidenceTest(unittest.TestCase):
    def test_doi_and_title_must_both_match(self):
        from scripts.collect.springer_abstracts import verified_abstract
        content=b'<meta name="citation_doi" content="10.1007/test"><h1>Study</h1><div id="abstract">This is a verified abstract containing enough words to qualify as article abstract text.</div>'
        self.assertTrue(verified_abstract(content,{'doi':'10.1007/test','title':'Study'}))
        self.assertFalse(verified_abstract(content,{'doi':'10.1007/other','title':'Study'}))
        self.assertFalse(verified_abstract(content,{'doi':'10.1007/test','title':'Other'}))

class OfficialAuthorFallbackTest(unittest.TestCase):
    def test_malformed_affiliation_requires_verified_fallback(self):
        from scripts.collect.update_catalog import download_rows, official_page_authors
        item={'type':'Poster','name':'A Study','virtualsite_url':'https://neurips.cc/virtual/2026/poster/1','speakers/authors':'A (Lab))'}
        page='<h1 class="event-title">A Study</h1><div class="event-organizers">A ⋅ B</div>'.encode()
        with self.assertRaises(ValueError):
            download_rows('NeurIPS','neurips.cc',[item])
        rows=download_rows('NeurIPS','neurips.cc',[item],author_fallback=lambda x:official_page_authors(page,x['name']))
        self.assertEqual(rows[0]['authors'],'A; B')
        with self.assertRaises(ValueError):
            official_page_authors(page,'Another paper')

class ArxivBatchTest(unittest.TestCase):
    def test_short_queries_do_not_skip_rows(self):
        from scripts.collect.emnlp_arxiv import next_batch, query_url
        rows=[{'Title':str(i)+' Long title '*14} for i in range(45)]
        start=0;seen=[]
        while start<len(rows):
            batch=next_batch(rows,start)
            self.assertLessEqual(len(query_url(batch)),1900)
            seen.extend(batch);start+=len(batch)
        self.assertEqual(seen,rows)
    def test_presenter_and_unique_title_are_required(self):
        from scripts.collect.emnlp_arxiv import match
        from lxml import etree
        entry=etree.fromstring(b'<entry xmlns="http://www.w3.org/2005/Atom"><title>Study</title><author><name>A Researcher</name></author><id>http://arxiv.org/abs/2601.12345v1</id><summary>Abstract</summary><published>2026-01-01T00:00:00Z</published></entry>')
        row={'Title':'Study','Presenters Name':'Researcher A','Paper ID #-track)':'1-MAIN'}
        self.assertIsNotNone(match(row,[entry]))
        self.assertIsNone(match(dict(row,**{'Presenters Name':'Other'}),[entry]))
        self.assertIsNone(match(row,[entry,entry]))

class SpringerTitleEncodingTest(unittest.TestCase):
    def test_html_entities_in_registry_title(self):
        from scripts.collect.springer_abstracts import verified_abstract
        page=b'<meta name="citation_doi" content="10.1007/test"><h1>Vision &amp; Language</h1><div id="abstract">This abstract contains enough words to verify publication identity without losing HTML-encoded title matches.</div>'
        self.assertTrue(verified_abstract(page,{'doi':'10.1007/test','title':'Vision &amp; Language'}))

class OpenAlexEvidenceTest(unittest.TestCase):
    def test_reject_ambiguous_wrong_person_and_future_records(self):
        from scripts.collect.emnlp_openalex import match
        work={'title':'A Study','authorships':[{'author':{'display_name':'Jane Doe'}}],
              'publication_date':'2026-01-01'}
        row={'title':'A Study'}
        self.assertIsNotNone(match(row,'Doe Jane',[work]))
        self.assertIsNone(match(row,'Other Person',[work]))
        self.assertIsNone(match(row,'Jane Doe',[work,work]))
        self.assertIsNone(match(row,'Jane Doe',[dict(work,publication_date='2099-01-01')]))
        self.assertIsNone(match({'title':'Different'},'Jane Doe',[work]))

    def test_abstract_requires_complete_nonconflicting_positions(self):
        from scripts.collect.emnlp_openalex import abstract_text
        self.assertEqual(abstract_text({'study':[1], 'A':[0]}),'A study')
        self.assertEqual(abstract_text({'A':[0], 'study':[2]}),'')
        self.assertEqual(abstract_text({'A':[0], 'B':[0]}),'')

    def test_multiple_presenters_must_all_match(self):
        from scripts.collect.emnlp_openalex import presenter_matches
        self.assertTrue(presenter_matches('Sumin Yu, Juhyeon Park',['Sumin Yu','Juhyeon Park','Taesup Moon']))
        self.assertFalse(presenter_matches('Sumin Yu, Unknown Person',['Sumin Yu','Juhyeon Park']))
        self.assertTrue(presenter_matches('Doe, Jane',['Jane Doe']))

class DataCiteEvidenceTest(unittest.TestCase):
    def candidate(self, **changes):
        a={'doi':'10.48550/arxiv.2601.12345','titles':[{'title':'A Study'}],
           'creators':[{'givenName':'Jane','familyName':'Doe','name':'Doe, Jane'}],
           'types':{'resourceTypeGeneral':'Preprint'},'state':'findable',
           'registered':'2026-01-02T00:00:00Z','publicationYear':2026,
           'descriptions':[{'descriptionType':'Abstract','description':'Verified text'}]}
        a.update(changes)
        return {'attributes':a}

    def test_identity_and_paper_type_required(self):
        from scripts.collect.datacite_enrichment import verified_match
        row={'title':'A Study','presenter':'Jane Doe'}
        self.assertEqual(verified_match(row,[self.candidate()])['authors'],['Jane Doe'])
        self.assertIsNone(verified_match(dict(row,presenter='Other Person'),[self.candidate()]))
        self.assertIsNone(verified_match(row,[self.candidate(types={'resourceTypeGeneral':'Dataset'})]))
        self.assertIsNone(verified_match(row,[self.candidate(registered='2099-01-01')]))
        self.assertIsNone(verified_match(row,[self.candidate(),self.candidate(doi='10.1234/other')]))

    def test_existing_author_and_no_invented_abstract(self):
        from scripts.collect.datacite_enrichment import verified_match
        row={'title':'A Study','authors':'Jane Doe; Another Author'}
        self.assertEqual(verified_match(row,[self.candidate(descriptions=[])])['abstract'],'')
        self.assertIsNone(verified_match({'title':'A Study','authors':''},[self.candidate()]))

class PMLRIdentityTest(unittest.TestCase):
    def test_shared_openreview_id_and_exact_published_title(self):
        from scripts.collect.official_programs_2026 import pmlr_identity
        page=b'<meta name="citation_title" content="New Title"><meta name="citation_author" content="Jane Doe"><a href="https://openreview.net/forum?id=abc">OpenReview</a><div id="abstract">Text</div>'
        self.assertEqual(pmlr_identity(page,'New Title')['openreview_id'],'abc')
        with self.assertRaises(ValueError):pmlr_identity(page,'Old Title')
        with self.assertRaises(ValueError):pmlr_identity(page.replace(b'openreview.net',b'openreview.net.invalid'),'New Title')
        with self.assertRaises(ValueError):pmlr_identity(page+b'<a href="https://openreview.net/forum?id=other">Other</a>','New Title')

    def test_broken_citation_attribute_requires_matching_visible_title(self):
        from scripts.collect.official_programs_2026 import pmlr_identity
        page=b'<meta name="citation_title" content="Position: "><meta name="citation_author" content="Jane Doe"><h1>Position: Full Title</h1><a href="https://openreview.net/forum?id=abc">OpenReview</a><div id="abstract">Text</div>'
        self.assertEqual(pmlr_identity(page,'Position: Full Title')['openreview_id'],'abc')
        with self.assertRaises(ValueError):pmlr_identity(page,'Unrelated Title')
