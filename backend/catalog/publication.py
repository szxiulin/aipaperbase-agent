"""Publication identity evidence, separate from annual source-list status."""
from functools import lru_cache
import json
from pathlib import Path

REVIEWS = Path(__file__).resolve().parents[2] / 'data/labels/publication_review.json'

@lru_cache(maxsize=4)
def _load(path: str, mtime: int) -> dict:
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    result = {}
    for row in rows:
        key = (row['venue'], int(row['year']), row['paper_id'])
        if key in result:
            raise ValueError('Duplicate publication review key')
        result[key] = row
    for row in rows:
        if row.get('publication_paper_id'):
            key = (row['publication_venue'], int(row['publication_year']), row['publication_paper_id'])
            if key not in result:
                result[key] = row
    return result


def review(venue: str, year: int, paper_id: str) -> dict:
    if not REVIEWS.exists():
        return {'status': 'not_individually_reviewed'}
    row = _load(str(REVIEWS), REVIEWS.stat().st_mtime_ns).get((venue, year, paper_id))
    return dict(row) if row else {'status': 'not_individually_reviewed'}
