import json,sqlite3,tempfile,unittest
from pathlib import Path
from backend.catalog.coverage import coverage_report,REPORT
from scripts.collect.reconcile_coverage import compare


class CoverageTests(unittest.TestCase):
    def test_source_local_numbers_do_not_prove_identity(self):
        counts,missing,_,_=compare([{'paper_id':'12','title':'New proceedings paper'}], [{'paper_id':'12','title':'Different accepted paper'}])
        self.assertEqual(counts['matched_reference_count'],0)
        self.assertEqual(len(missing),1)

    def test_ambiguous_titles_are_not_accepted(self):
        counts,_,ambiguous,_=compare([{'title':'Shared title'}],[{'title':'Shared title'},{'title':'Shared title'}])
        self.assertEqual(counts['matched_reference_count'],0)
        self.assertEqual(len(ambiguous),1)

    def test_doi_matching_retains_local_extras(self):
        counts,missing,_,extra=compare([{'doi':'10.1/A','title':'Revised title'}],[{'doi':'10.1/a','title':'Old title'},{'title':'Presentation'}])
        self.assertEqual(counts['matched_reference_count'],1)
        self.assertEqual(missing,[])
        self.assertEqual(len(extra),1)

    def test_snapshot_is_marked_stale_after_database_update(self):
        c=sqlite3.connect(':memory:')
        try:
            c.execute('CREATE TABLE data_releases(release_id,status,created_at)')
            c.execute("INSERT INTO data_releases VALUES ('new','ready','today')")
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'report.json';p.write_text(json.dumps({'catalog_release_id':'old','rows':[]}))
                self.assertTrue(coverage_report(c,p)['stale'])
                p.write_text(json.dumps({'catalog_release_id':'new','rows':[]}))
                self.assertFalse(coverage_report(c,p)['stale'])
        finally:c.close()

    def test_frozen_report_distinguishes_unknown_from_zero(self):
        data=json.loads(REPORT.read_text());rows=data['rows']
        self.assertEqual(len(rows),124)
        self.assertEqual(len({(r['venue'],r['year']) for r in rows}),124)
        self.assertEqual(sum(r['local_record_count'] for r in rows),data['corpus']['source_record_count'])
        for row in rows:
            if row['status'] in ('unverified','not_applicable','out_of_scope','supplement'):
                self.assertIsNone(row['reference_count'])
                self.assertIsNone(row['unmatched_reference_count'])
            if row['status'] in ('aligned','registry_aligned'):
                self.assertEqual(row['unmatched_reference_count'],0)
                self.assertEqual(row['ambiguous_reference_count'],0)
                self.assertEqual(row['local_only_count'],0)
        self.assertEqual(sum(r['scope']=='primary' for r in rows),120)
