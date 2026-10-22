"""Validate an IEEE Xplore CSV export without network or catalog writes."""
from __future__ import annotations

import csv
from datetime import datetime
import html
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


def online_date(value: str) -> str:
    """Keep publication dates separate from issue dates and ingestion dates."""
    value = value.strip()
    if not value:
        return ''
    return datetime.strptime(value, '%d %b %Y').date().isoformat()


def title_identity(value: str) -> str:
    value = re.sub(r'</?(?:i|b|u|sup|sub|em|strong)(?:\s[^>]*)?>', '', value, flags=re.I)
    return re.sub(r'[^\w]', '', html.unescape(value).casefold())


def document_url(pdf_link: str) -> str:
    number = parse_qs(urlsplit(pdf_link).query).get('arnumber', [''])[0]
    return 'https://ieeexplore.ieee.org/document/' + number if number.isdigit() else ''


def read_export(path: Path, *, title: str, year: int, expected: int) -> list[dict]:
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'Document Title', 'Authors', 'Publication Title', 'Publication Year',
                    'Abstract', 'DOI', 'Online Date', 'Issue Date', 'Document Identifier', 'PDF Link'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Not the expected IEEE results export')
        rows = list(reader)
    if len(rows) != expected:
        raise ValueError('Export row count differs from the observed result count')
    seen = set()
    for row in rows:
        doi = row['DOI'].strip().casefold()
        if not doi.startswith('10.1109/') or doi in seen:
            raise ValueError('Missing, invalid or duplicate DOI')
        seen.add(doi)
        if row['Publication Title'] != title or row['Publication Year'] != str(year):
            raise ValueError('Publication title/year filter mismatch')
        if row['Document Identifier'] not in ('IEEE Journals', 'IEEE Early Access Articles'):
            raise ValueError('Unexpected document category')
        row['doi_normalized'] = doi
        row['first_online_date'] = online_date(row['Online Date'])
        row['official_document_url'] = document_url(row['PDF Link'])
    return rows


def match_record(connection, row: dict, *, venue: str, year: int) -> dict | None:
    matches = connection.execute(
        'SELECT * FROM paper_records WHERE venue=? AND year=? AND lower(doi)=?',
        (venue, year, row['doi_normalized'])).fetchall()
    if len(matches) > 1:
        raise ValueError('DOI is ambiguous within the source-year')
    if not matches:
        return None
    record = dict(matches[0])
    if title_identity(record['title']) != title_identity(row['Document Title']):
        raise ValueError('DOI and title evidence disagree')
    return record
