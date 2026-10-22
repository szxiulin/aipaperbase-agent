"""Regression checks for evidence-based incremental catalog updates."""
import unittest
from dataclasses import asdict
from scripts.collect import update_catalog as u


def row(pid, title='Paper', **kw):
    return u.normalize(asdict(u.base.Paper(paper_id=pid, title=title, venue='ICML',
                         venue_type='conference', year=2026, track='main', authors='Author', **kw)))


class IncrementalCatalogTest(unittest.TestCase):
    def test_html_entities_do_not_duplicate_a_paper(self):
        merged, report = u.merge_rows([row('old', 'Vision & Language')],
                                      [row('new', 'Vision &amp; Language')])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['paper_id'], 'old')
        self.assertFalse(report['conflicts'])

    def test_identifier_title_conflict_is_quarantined(self):
        merged, report = u.merge_rows([row('id', 'One')], [row('id', 'Two')])
        self.assertEqual(merged[0]['title'], 'One')
        self.assertEqual(len(report['conflicts']), 1)

    def test_existing_abstract_and_source_are_preserved(self):
        old = row('old'); u.set_abstract(old, 'Original evidence', 'https://example.org/old', 'Original')
        new = row('new'); u.set_abstract(new, 'New evidence', 'https://example.org/new', 'New')
        merged, _ = u.merge_rows([old], [new])
        self.assertEqual(merged[0]['abstract'], 'Original evidence')
        self.assertEqual(merged[0]['abstract_source_name'], 'Original')

    def test_generic_program_url_is_not_a_paper_identity(self):
        items = [{'type':'Poster','name':'Study','virtualsite_url':'https://neurips.cc/virtual/2026'}]
        self.assertEqual(u.download_rows('NeurIPS','neurips.cc',items), [])

    def test_ijcai_submission_number_is_not_a_doi(self):
        old = row('77', 'Study', doi='10.24963/ijcai.2026/77', source_name='IJCAI accepted papers',
                  source_url='https://example.org/accepted')
        old['venue'] = 'IJCAI'
        new = dict(old, paper_id='12', doi='10.24963/ijcai.2026/12', source_name='IJCAI Proceedings')
        merged, report = u.merge_rows([old], [new])
        self.assertEqual(merged[0]['paper_id'], '77')
        self.assertEqual(merged[0]['doi'], '10.24963/ijcai.2026/12')
        self.assertFalse(report['conflicts'])
        merged, _ = u.merge_rows([old], [])
        self.assertEqual(merged[0]['doi'], '')
        self.assertEqual(merged[0]['paper_url'], old['source_url'])

class OfficialAbstractTest(unittest.TestCase):
    def test_ijcai_abstract_requires_matching_title(self):
        text = 'This is an official abstract with enough text to test accurate extraction from the proceedings.'
        page = ('<meta name="citation_conference_title" content="International Joint Conference on Artificial Intelligence">'
                '<h2>Verified title</h2><hr><div class="row"><div class="col-md-12">'+text+
                '</div><div>Keywords should be excluded</div></div>').encode()
        self.assertEqual(u.enrich.extract_official_page_abstract(page, 'Verified title'), text)
        self.assertEqual(u.enrich.extract_official_page_abstract(page, 'Another paper'), '')


class AffiliationParsingTest(unittest.TestCase):
    def test_nested_affiliations_leave_only_names(self):
        self.assertEqual(u.strip_affiliations('A Name (Institute (AI)), B Name (Lab)'), 'A Name, B Name')
    def test_unbalanced_affiliations_are_rejected(self):
        with self.assertRaises(ValueError):
            u.strip_affiliations('A Name (Institute')
        with self.assertRaises(ValueError):
            u.strip_affiliations('A Name Institute)')
    def test_mixed_width_parentheses(self):
        self.assertEqual(u.strip_affiliations('A Name (Institute (AI）), B Name'), 'A Name, B Name')
