import csv
import tempfile
import unittest
from pathlib import Path

from scripts.collect.ieee_export import document_url, online_date, read_export, title_identity


class IEEEExportTests(unittest.TestCase):
    def test_publication_date_uses_online_date(self):
        self.assertEqual(online_date('29 Jan 2026'), '2026-01-29')
        self.assertEqual(online_date(''), '')
        with self.assertRaises(ValueError):
            online_date('July 2026')

    def test_title_markup_is_not_an_identity_conflict(self):
        self.assertEqual(title_identity('<i>EasyREG</i> : Easy Registration'),
                         title_identity('EasyREG: Easy Registration'))
        self.assertNotEqual(title_identity('Study A'), title_identity('Study B'))

    def test_pdf_metadata_link_does_not_require_pdf_download(self):
        self.assertEqual(document_url('https://example.org/stamp?arnumber=11367645'),
                         'https://ieeexplore.ieee.org/document/11367645')
        self.assertEqual(document_url('https://example.org/stamp'), '')

    def test_export_must_match_observed_count_and_filter(self):
        fields = ['Document Title', 'Authors', 'Publication Title', 'Publication Year',
                  'Abstract', 'DOI', 'Online Date', 'Issue Date', 'Document Identifier', 'PDF Link']
        # Test scratch is retained, consistent with the project's Trash-only rule.
        path = Path(tempfile.mkdtemp()) / 'export.csv'
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerow(dict(zip(fields, ['Study', 'A. Author', 'TVCG', '2026', '',
                '10.1109/test.1', '20 Oct 2025', 'January 2026', 'IEEE Journals', ''])))
        rows = read_export(path, title='TVCG', year=2026, expected=1)
        self.assertEqual(rows[0]['first_online_date'], '2025-10-20')
        with self.assertRaises(ValueError):
            read_export(path, title='TVCG', year=2026, expected=2)
        with self.assertRaises(ValueError):
            read_export(path, title='TIP', year=2026, expected=1)
