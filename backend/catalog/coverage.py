"""Frozen coverage audit, explicitly tied to a catalog release."""
import json
from pathlib import Path

REPORT = Path(__file__).resolve().parents[2] / 'data/catalog/reports/coverage_20261021_round5.json'
MILESTONE_REPORT = REPORT.with_name('coverage_20261022_milestone.json')


def coverage_report(connection, path=None):
    path = Path(path) if path is not None else (MILESTONE_REPORT if MILESTONE_REPORT.exists() else REPORT)
    if not path.exists():
        return {'available': False, 'stale': True, 'rows': []}
    report = json.loads(path.read_text(encoding='utf-8'))
    release = connection.execute("SELECT release_id FROM data_releases WHERE status='ready' ORDER BY created_at DESC LIMIT 1").fetchone()
    report['available'] = True
    report['stale'] = not release or release[0] != report['catalog_release_id']
    return report
