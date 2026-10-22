import unittest
from scripts.collect.free_abstracts import accepts

class FreeAbstractIdentityTest(unittest.TestCase):
    def test_requires_doi_and_title_to_agree(self):
        row={'title':'A Study'}
        paper={'title':'A Study','abstract':'Evidence','externalIds':{'DOI':'10.1234/test'}}
        self.assertTrue(accepts('DOI:10.1234/TEST',row,paper))
        self.assertFalse(accepts('DOI:10.1234/another',row,paper))
        self.assertFalse(accepts('DOI:10.1234/test',{'title':'Another Study'},paper))
    def test_missing_abstract_is_not_filled(self):
        self.assertFalse(accepts('DOI:10.1234/a',{'title':'Study'},None))
        self.assertFalse(accepts('DOI:10.1234/a',{'title':'Study'},{'title':'Study','abstract':None}))
    def test_arxiv_versions_and_title_markup(self):
        self.assertTrue(accepts('ARXIV:2601.12345v2',{'title':'β Learning'},
            {'title':'<i>β</i> Learning','abstract':'Evidence','externalIds':{'ArXiv':'2601.12345'}}))
