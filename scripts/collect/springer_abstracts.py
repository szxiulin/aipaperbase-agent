"""Collect free Springer abstracts with exact DOI/title checks, leaving CSVs untouched."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from html import unescape
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.collect.update_catalog import Fetcher, read_csv, enrich, html


def verified_abstract(content, row):
    document = html.fromstring(content)
    dois = document.xpath('//meta[@name="citation_doi"]/@content')
    if not any(value.casefold() == row['doi'].casefold() for value in dois):
        return ''
    return enrich.extract_official_page_abstract(content, unescape(row['title']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=range(1, 7), default=3)
    args = parser.parse_args()
    run = args.run
    fetcher = Fetcher(run / 'cache')
    output = run / 'springer_results.json'
    results = json.loads(output.read_text()) if output.exists() else []
    done = {(x['year'], x['paper_id']) for x in results}
    rows = [row for path in (ROOT/'data/catalog/journals/IJCV').glob('*.csv')
            for row in read_csv(path) if not row['abstract'] and row['doi']
            and (row['year'], row['paper_id']) not in done]

    def collect(row):
        result = {key: row[key] for key in ('venue', 'year', 'paper_id', 'title', 'doi')}
        result['url'] = 'https://link.springer.com/article/' + row['doi']
        try:
            result['abstract'] = verified_abstract(fetcher.get(result['url']), row)
            result['status'] = 'filled' if result['abstract'] else 'identity_or_abstract_missing'
        except Exception as exc:
            result.update(status='error', error=str(exc)[:160])
        return result

    for start in range(0, len(rows), 25):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            batch = list(pool.map(collect, rows[start:start+25]))
        results.extend(batch)
        output.write_text(json.dumps(results, ensure_ascii=False))
        print(len(results), Counter(x['status'] for x in results), flush=True)
        if start == 0 and sum(x['status']=='filled' for x in batch) < min(15, len(batch)):
            print('Stopping after low-yield probe', flush=True)
            break
        if sum('403' in x.get('error', '') or '429' in x.get('error', '') for x in batch) > 3:
            print('Stopping on access restrictions', flush=True)
            break
        time.sleep(1)


if __name__ == '__main__':
    main()
