"""Read-only corpus integrity audit and frozen input queue for later classification."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.catalog.metrics import corpus_summary


def text_title(value):
    return html.unescape(re.sub(r'</?(?:i|b|u|sup|sub|em|strong)(?:\s[^>]*)?>', '', value, flags=re.I))


def input_hash(title, abstract):
    return hashlib.sha256((title + '\0' + abstract).encode()).hexdigest()


def classification_group(title, abstract, duplicate_candidate=False):
    suspect = bool(re.match(r'^(corrections? to\b|erratum\b|corrigendum\b|editorial\b|'
                            r'guest editorial\b|.*\bpaper reviewers\s*$)', title, re.I))
    if not title.strip() or suspect or duplicate_candidate:
        return 'review_first'
    return 'with_abstract' if abstract.strip() else 'title_only'


def run(database, output):
    connection = sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    scalar = lambda query: connection.execute(query).fetchone()[0]
    checks = {
        'integrity_check': scalar('PRAGMA integrity_check'),
        'foreign_key_violations': len(connection.execute('PRAGMA foreign_key_check').fetchall()),
        'records_without_membership': scalar('''SELECT count(*) FROM paper_records p
            LEFT JOIN entity_memberships m USING(record_id) WHERE m.entity_id IS NULL'''),
        'entities_without_canonical_record': scalar('''SELECT count(*) FROM paper_entities e
            LEFT JOIN paper_records p ON p.record_id=e.canonical_record_id WHERE p.record_id IS NULL'''),
        'entities_without_area_state': scalar('''SELECT count(*) FROM paper_entities e
            LEFT JOIN entity_area_assignments a USING(entity_id) WHERE a.entity_id IS NULL'''),
        'pending_with_direction': scalar('''SELECT count(*) FROM entity_area_assignments a
            JOIN entity_direction_assignments d USING(entity_id) WHERE a.area_id='pending_classification' '''),
        'doi_split_across_entities': scalar('''SELECT count(*) FROM (SELECT lower(p.doi)
            FROM paper_records p JOIN entity_memberships m USING(record_id) WHERE trim(p.doi)!=''
            GROUP BY lower(p.doi) HAVING count(DISTINCT m.entity_id)>1)'''),
        'blank_research_titles': scalar('''SELECT count(*) FROM paper_entities e
            JOIN paper_records p ON p.record_id=e.canonical_record_id
            JOIN entity_area_assignments a USING(entity_id)
            WHERE a.is_non_paper=0 AND trim(p.title)='' '''),
    }
    critical = [key for key, value in checks.items()
                if (value != 'ok' if key == 'integrity_check' else value != 0)]
    corpus = corpus_summary(connection)
    proof_records = set()
    with gzip.open(output / 'publication_items_20261021.csv.gz', 'rt', encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            proof_records.update(filter(None, row['matched_record_ids'].split(' | ')))
    confirmed_entities = {row[0] for row in connection.execute('''SELECT entity_id,record_id
        FROM entity_memberships''') if row[1] in proof_records}
    review_records = {value for row in connection.execute('''SELECT left_record_id,right_record_id
        FROM entity_review_candidates WHERE review_status='pending' ''') for value in row}
    review_entities = {row[0] for row in connection.execute('SELECT entity_id,record_id FROM entity_memberships')
                       if row[1] in review_records}
    rows = [dict(row) for row in connection.execute('''SELECT e.entity_id,e.canonical_record_id,
        p.paper_id,p.venue,p.year,p.title,p.authors,p.abstract,p.doi,p.paper_url,
        p.abstract_source_name,p.abstract_source_url,a.area_id
        FROM paper_entities e JOIN paper_records p ON p.record_id=e.canonical_record_id
        JOIN entity_area_assignments a USING(entity_id) WHERE a.is_non_paper=0
        ORDER BY e.entity_id''')]
    pending = []
    for row in rows:
        if row['area_id'] != 'pending_classification':
            continue
        row['input_title'] = text_title(row['title'])
        row['input_sha256'] = input_hash(row['input_title'], row['abstract'])
        row['publication_evidence'] = 'confirmed_in_saved_inventory' if row['entity_id'] in confirmed_entities else 'not_fully_verified'
        row['duplicate_candidate'] = row['entity_id'] in review_entities
        row['classification_group'] = classification_group(row['input_title'], row['abstract'], row['duplicate_candidate'])
        pending.append(row)
    assert len(pending) == corpus['pending_count']
    fields = ['entity_id','canonical_record_id','paper_id','venue','year','input_title','authors',
              'abstract','doi','paper_url','abstract_source_name','abstract_source_url',
              'input_sha256','publication_evidence','duplicate_candidate','classification_group']
    queue = output / 'classification_queue_20261021.csv.gz'
    with gzip.open(queue, 'wt', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(pending)
    missing = Counter(row['venue'] for row in rows if not row['abstract'].strip())
    missing_authors = Counter(row['venue'] for row in rows if not row['authors'].strip())
    report = {
        'cutoff': '2026-10-21',
        'catalog_release_id': scalar("SELECT release_id FROM data_releases WHERE status='ready' ORDER BY created_at DESC LIMIT 1"),
        'checks': checks, 'critical_failures': critical, 'corpus': corpus,
        'classification_groups': dict(Counter(row['classification_group'] for row in pending)),
        'pending_missing_abstract': sum(not row['abstract'].strip() for row in pending),
        'pending_missing_authors': sum(not row['authors'].strip() for row in pending),
        'pending_publication_evidence': dict(Counter(row['publication_evidence'] for row in pending)),
        'saved_inventory_confirmed_research_entities': sum(row['entity_id'] in confirmed_entities for row in rows),
        'missing_abstract_by_venue': dict(missing.most_common()),
        'missing_authors_by_venue': dict(missing_authors.most_common()),
        'entity_review_candidates': scalar("SELECT count(*) FROM entity_review_candidates WHERE review_status='pending'"),
        'queue_file': queue.name,
        'queue_sha256': hashlib.sha256(queue.read_bytes()).hexdigest(),
        'limitations': [
            'No semantic accuracy or independent human gold-label evaluation is implied.',
            'Unverified publication coverage is not proof that a record is unpublished.',
            'Publisher year may differ from first-online year; old IDs and catalog years are retained.',
            'This is a metadata corpus; availability of metadata does not imply downloaded full text.',
        ],
    }
    (output / 'data_readiness_20261021.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ['critical_failures','classification_groups',
        'pending_missing_abstract','pending_missing_authors','pending_publication_evidence',
        'saved_inventory_confirmed_research_entities']}, ensure_ascii=False))
    connection.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=ROOT / 'data/database/catalog.sqlite')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/catalog/reports')
    args = parser.parse_args()
    result = run(args.database, args.output)
    if result['critical_failures']:
        raise SystemExit('Integrity check failed: ' + ', '.join(result['critical_failures']))
