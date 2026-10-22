"""Collect free DataCite metadata with exact title and author identity checks.

Only fills evidence files in --run. No credentials, catalog writes or model APIs.
The frozen queue puts the previous 253 unqueried EMNLP records first.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.collect.update_catalog import title_key, now
from scripts.collect.emnlp_openalex import presenter_matches
from scripts.collect.emnlp_arxiv import name_key


def authors_of(attributes):
    result = []
    for creator in attributes.get('creators', []):
        name = ' '.join(str(creator.get(k) or '').strip() for k in ('givenName', 'familyName')).strip()
        result.append(name or str(creator.get('name') or '').strip())
    return result if result and all(result) else []


def verified_match(row, candidates):
    found = {}
    for item in candidates:
        a = item['attributes']
        authors = authors_of(a)
        if not any(title_key(t['title']) == title_key(row['title']) for t in a.get('titles', [])):
            continue
        if a.get('state') != 'findable' or not authors:
            continue
        if a.get('types', {}).get('resourceTypeGeneral') not in {'Preprint', 'JournalArticle', 'ConferencePaper', 'Text'}:
            continue  # A dataset or software with the paper's name is not its byline.
        registered = str(a.get('registered') or '')[:10]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', registered) or registered > date.today().isoformat():
            continue
        if int(a.get('publicationYear') or 9999) > date.today().year:
            continue
        if row.get('presenter'):
            identity = presenter_matches(row['presenter'], authors)
        else:
            identity = any(name_key(old) and name_key(old) == name_key(new)
                           for old in row.get('authors', '').split(';') for new in authors)
        if not identity:
            continue
        abstracts = [x['description'] for x in a.get('descriptions', []) if x.get('descriptionType') == 'Abstract']
        found[a['doi']] = {'doi': a['doi'], 'authors': authors,
            'abstract': abstracts[0] if len(abstracts) == 1 else '',
            'source_url': 'https://doi.org/' + a['doi'], 'registered': registered,
            'publication_year': a['publicationYear']}
    return next(iter(found.values())) if len(found) == 1 else None


def query_url(batch):
    # Quoted title phrases retrieve candidates; normalized exact comparison is
    # performed locally. Escape only the query-string literal delimiters.
    titles = [x['title'].replace('\\', '\\\\').replace('"', '\\"') for x in batch]
    query = ' OR '.join('titles.title:"' + title + '"' for title in titles)
    return 'https://api.datacite.org/dois?' + urlencode({'query': query, 'page[size]': 1000})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run
    queue_path = run / 'datacite_queue.json'
    rows = json.loads(queue_path.read_text())
    queue_hash = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    cache = run / 'datacite_cache'
    cache.mkdir(parents=True, exist_ok=True)
    progress_path = run / 'datacite_progress.json'
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {'processed': 0, 'queue_sha256': queue_hash}
    assert progress['queue_sha256'] == queue_hash, 'Queue changed since checkpoint'
    result_path = run / 'datacite_matches.json'
    results = json.loads(result_path.read_text()) if result_path.exists() else []
    start = progress['processed']
    skipped = progress.get('skipped_known_nonpaper', 0)
    exclusions_path = run / 'datacite_exclusions.json'
    exclusions = json.loads(exclusions_path.read_text()) if exclusions_path.exists() else []
    excluded = {(x['source_file'], x['paper_id']) for x in exclusions}
    while start < len(rows):
        batch = rows[start:start + 50]
        while len(batch) > 1 and len(query_url(batch)) > 6500:
            batch = batch[:-1]
        active = [x for x in batch if (x['source_file'], x['paper_id']) not in excluded]
        url = query_url(active)
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        try:
            if active and not path.exists():
                with urlopen(url, timeout=40) as response:
                    payload = response.read()
                path.write_bytes(payload)
                path.with_suffix('.source.json').write_text(json.dumps({'url': url, 'fetched_at': now(),
                    'sha256': hashlib.sha256(payload).hexdigest()}))
            data = json.loads(path.read_bytes()) if active else {'meta': {'total': 0}, 'data': []}
            if data['meta']['total'] > 1000:
                raise ValueError('Candidate set truncated; batch needs review')
            for row in active:
                result = verified_match(row, data['data'])
                if result:
                    results.append({**result, 'paper_id': row['paper_id'], 'source_file': row['source_file'],
                                    'title': row['title'], 'query_cache': path.name})
            start += len(batch)
            skipped += len(batch) - len(active)
            result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
            progress_path.write_text(json.dumps({'processed': start, 'total': len(rows),
                'matches': len(results), 'queue_sha256': queue_hash,
                'skipped_known_nonpaper': skipped, 'queried': start - skipped}))
            print('processed', start, '/', len(rows), 'matched', len(results), flush=True)
        except Exception as exc:
            (run / 'datacite_error.json').write_text(json.dumps({'start': start, 'error': str(exc)[:250]}))
            print('STOP', str(exc)[:250], flush=True)
            break
        time.sleep(1)


if __name__ == '__main__':
    main()
