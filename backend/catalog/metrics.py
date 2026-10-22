"""Live catalog counts. Ratios use canonical research entities, never source rows."""
from __future__ import annotations
import sqlite3

# The release remains incomplete after the calendar turns: update only after a corpus audit.
LAST_COMPLETE_YEAR = 2025


def coverage(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("""SELECT p.year, COUNT(*) AS total,
        SUM(CASE WHEN a.area_id IS NOT NULL AND a.area_id <> 'pending_classification'
                 THEN 1 ELSE 0 END) AS classified
        FROM paper_entities e JOIN paper_records p ON p.record_id = e.canonical_record_id
        LEFT JOIN entity_area_assignments a ON a.entity_id = e.entity_id
        WHERE COALESCE(a.is_non_paper, 0) = 0 GROUP BY p.year ORDER BY p.year""").fetchall()
    return [{"year": r[0], "total": r[1], "classified": r[2], "pending": r[1]-r[2],
             "coverage_rate": round(100*r[2]/r[1], 2) if r[1] else None,
             "year_status": "complete_window" if r[0] <= LAST_COMPLETE_YEAR else "collecting"}
            for r in rows]


def corpus_summary(connection: sqlite3.Connection) -> dict:
    row = connection.execute("""SELECT COUNT(*) AS entities,
        SUM(CASE WHEN COALESCE(a.is_non_paper,0)=1 THEN 1 ELSE 0 END) AS non_papers,
        SUM(CASE WHEN COALESCE(a.is_non_paper,0)=0 AND TRIM(COALESCE(p.abstract,''))='' THEN 1 ELSE 0 END) AS missing_abstracts,
        SUM(CASE WHEN COALESCE(a.is_non_paper,0)=0 AND TRIM(COALESCE(p.authors,''))='' THEN 1 ELSE 0 END) AS missing_authors
        FROM paper_entities e JOIN paper_records p ON p.record_id=e.canonical_record_id
        LEFT JOIN entity_area_assignments a ON a.entity_id=e.entity_id""").fetchone()
    records = connection.execute("SELECT COUNT(*) FROM paper_records").fetchone()[0]
    years = coverage(connection)
    total = sum(r['total'] for r in years)
    classified = sum(r['classified'] for r in years)
    directions = connection.execute("""SELECT COUNT(*) FROM entity_direction_assignments d
        JOIN entity_area_assignments a USING(entity_id) WHERE a.is_non_paper=0""").fetchone()[0]
    return {"source_record_count": records, "entity_count": row[0], "non_paper_count": row[1] or 0,
            "merged_extra_record_count": records-row[0], "research_paper_count": total,
            "missing_abstract_count": row[2] or 0, "missing_author_count": row[3] or 0,
            "abstract_count": total-(row[2] or 0), "classified_count": classified,
            "pending_count": total-classified, "classification_coverage_rate": round(100*classified/total,2) if total else None,
            "direction_classified_count": directions,
            "pending_direction_count": classified-directions,
            "direction_coverage_rate": round(100*directions/total,2) if total else None,
            "count_unit": "canonical_research_entities", "by_year": years,
            "last_complete_year": LAST_COMPLETE_YEAR,
            "note": "Counts describe the local corpus, not all published papers. Missing fields use canonical records."}
