"""Enrich official EMNLP records using unauthenticated OpenAlex metadata.

Only unique exact titles with a matching official presenter are accepted.
Writes resumable evidence to --run; never changes the catalog or reads keys.
"""
import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.collect.update_catalog import read_csv, title_key, now
from scripts.collect.emnlp_arxiv import name_key


def abstract_text(index):
    positions = {}
    for word, indexes in (index or {}).items():
        for position in indexes:
            if position in positions:
                return ''
            positions[position] = word
    if not positions or set(positions) != set(range(len(positions))):
        return ''
    return ' '.join(positions[i] for i in range(len(positions)))


def match(row, presenter, works):
    candidates = []
    for work in works:
        authors = [x['author']['display_name'] for x in work['authorships']]
        published = work.get('publication_date') or ''
        if (title_key(work['title']) == title_key(row['title'])
                and presenter_matches(presenter, authors)
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}', published)
                and published <= date.today().isoformat()):
            candidates.append(work)
    # Multiple indexed versions need a separate review, not arbitrary selection.
    return candidates[0] if len(candidates) == 1 else None


def presenter_matches(presenter, authors):
    names = [name_key(a) for a in authors]
    if name_key(presenter) and name_key(presenter) in names:
        return True
    # Some official cells contain several full names. Require every listed
    # presenter, and do not interpret a surname/given-name comma as two people.
    parts = [name_key(x.strip()) for x in re.split(r'[,;]', presenter)]
    return len(parts) > 1 and all(len(p) >= 2 and p in names for p in parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--schedule', type=Path, required=True)
    args = parser.parse_args()
    run = args.run
    cache = run / 'openalex_cache'
    cache.mkdir(parents=True, exist_ok=True)
    with args.schedule.open() as f:
        presenters = {x['Paper ID #-track)']: x['Presenters Name'] for x in csv.DictReader(f)}
    rows = [x for x in read_csv(run / 'baseline/data/catalog/conferences/EMNLP/emnlp_2026.csv')
            if not x['authors'] or not x['abstract']]
    output = run / 'openalex_matches.json'
    progress = run / 'openalex_progress.json'
    matches = json.loads(output.read_text()) if output.exists() else []
    start = json.loads(progress.read_text())['processed'] if progress.exists() else 0
    while start < len(rows):
        batch = rows[start:start + 20]
        query = '|'.join(re.sub(r'[^\w\s]', ' ', x['title']) for x in batch)
        url = 'https://api.openalex.org/works?' + urlencode({
            'filter': 'title.search:' + query, 'per-page': 200,
            'select': 'id,title,authorships,abstract_inverted_index,publication_date,doi,primary_location'})
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        try:
            if not path.exists():
                with urlopen(url, timeout=35) as response:
                    payload = response.read()
                path.write_bytes(payload)
                path.with_suffix('.source.json').write_text(json.dumps({'url': url, 'fetched_at': now(),
                    'sha256': hashlib.sha256(payload).hexdigest()}))
            data = json.loads(path.read_bytes())
            if data['meta']['count'] > 200:
                raise ValueError('Truncated candidate set; review batch before continuing')
            for row in batch:
                presenter = presenters[row['paper_id'].removeprefix('program-')]
                work = match(row, presenter, data['results'])
                if work:
                    matches.append({'paper_id': row['paper_id'], 'title': row['title'],
                        'authors': [x['author']['display_name'] for x in work['authorships']],
                        'abstract': abstract_text(work.get('abstract_inverted_index')),
                        'source_url': work['id'], 'presenter': presenter,
                        'publication_date': work['publication_date'], 'query_cache': path.name})
            start += len(batch)
            output.write_text(json.dumps(matches, ensure_ascii=False, indent=2))
            progress.write_text(json.dumps({'processed': start, 'total': len(rows), 'matched': len(matches)}))
            print('processed', start, 'matched', len(matches), flush=True)
        except Exception as exc:
            (run / 'openalex_error.json').write_text(json.dumps({'start': start, 'error': str(exc)[:250]}))
            print('STOP', str(exc)[:250], flush=True)
            break
        time.sleep(1)


if __name__ == '__main__':
    main()
