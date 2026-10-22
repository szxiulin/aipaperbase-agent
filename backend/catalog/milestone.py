"""Read the local data milestone only when it matches the active catalog."""
import json
import sqlite3
from pathlib import Path

REPORT = Path(__file__).resolve().parents[2] / 'data/catalog/reports/local_milestone_20261022.json'


def current_milestone(connection):
    if not REPORT.exists():
        return None
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    release = connection.execute("SELECT release_id FROM data_releases WHERE status='ready' ORDER BY created_at DESC LIMIT 1").fetchone()
    if not release:
        return None
    if release[0] == report.get('catalog_release_id'):
        return report
    expected = report.get('content_identity')
    if not expected:
        return None
    # A rebuild has a new timestamp, even when source data and labels are identical.
    try:
        source = connection.execute("SELECT source_digest FROM data_releases WHERE release_id=?", (release[0],)).fetchone()
        labels = connection.execute("SELECT areas_sha256, directions_sha256 FROM label_releases WHERE catalog_release_id=? AND status='ready' ORDER BY created_at DESC LIMIT 1", (release[0],)).fetchone()
    except sqlite3.OperationalError:
        return None
    actual = {'source_digest': source[0], 'areas_sha256': labels[0], 'directions_sha256': labels[1]} if source and labels else None
    return report if actual == expected else None
