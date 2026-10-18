"""Flag catalog records that are not research papers, so direction statistics can exclude them.

Rules match whole-title shapes of journal/conference front matter and notices only. They were
checked by hand against every candidate in the 2026-10-16 draft (see Sprint-029); titles such as
"Preface: A Data-driven Volumetric Prior ...", "Reconfiguring Proportional Committees" or
"Optimization without Retraction ..." are real papers and must not match.

Flags never delete or relabel anything; they are an overlay consumed by the draft builder.
"""
import argparse
import csv
import re
import sqlite3
from pathlib import Path

RULES = [
    ("front_matter", r"^(editorial board|front cover|back cover|table of contents?|front matter|blank page|preface"
                     r"|title tbd|ieee computer society has you covered!|get published in the new ieee open journal.*"
                     r"|publications seek 20\d\d editors in chief"
                     r"|ieee transactions on [a-z ]+( publication information|: 20\d\d ieee .*)?)$"),
    ("front_matter", r"^vis 20\d\d contents$"),
    ("committee_or_reviewers", r"^((ieee )?(vr|ismar|vis) 20\d\d .*(committee|committees|supercommittee)( members)?( for journal papers)?"
                               r"|vgtc executive committee|ieee visualization and graphics technical committee \(vgtc\)"
                               r"|(ieee )?(vr|ismar|vis) 20\d\d (- )?(paper )?reviewers( for journal papers)?"
                               r"|20\d\d reviewers list(\s*<sup>\*</sup>|\*)?|reviewer summary for transactions on image processing)$"),
    ("award_or_statement", r"^((20\d\d )?(ieee )?vgtc visualization .*award|ieee (vr|ismar) 20\d\d visualization and graphics technical (committee|community) \(vgtc\) statement)$"),
    ("editorial", r"^(editorial\b.*|guest editorial\b.*|editor['’]s note\b.*|editorial introduction\b.*|preface to the special issue\b.*"
                  r"|.*\bintroduction to the special (issue|section)\b.*|ieee (vr|ismar) 20\d\d introducing the special issue"
                  r"|(ieee (vr|ismar) 20\d\d )?message from .*|welcome: message from .*|tog \d+\(\d+\) editorial"
                  r"|farewell and new eic introduction|in memoriam: .*)$"),
    ("correction_notice", r"^(erratum|errata|corrigendum|correction to)\b.*"),
    ("retraction", r"^(notice of retraction|retraction notice|retracted)\b.*"),
]
COMPILED = [(kind, re.compile(pattern, re.I)) for kind, pattern in RULES]


def classify(title: str) -> str:
    t = re.sub(r"\s+", " ", title.replace("&amp;", "&")).strip().rstrip(".")
    for kind, pattern in COMPILED:
        if pattern.match(t):
            return kind
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path("data/database/catalog.sqlite"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute("SELECT e.entity_id, e.canonical_title, r.venue, r.year, length(trim(r.abstract)) > 0 "
                       "FROM paper_entities e JOIN paper_records r ON r.record_id = e.canonical_record_id").fetchall()
    flagged = [(e, classify(t), t, v, y, int(a)) for e, t, v, y, a in rows if classify(t)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "record_kind", "title", "venue", "year", "has_abstract"])
        w.writerows(sorted(flagged, key=lambda r: (r[1], r[3], r[2])))
    print(f"flagged {len(flagged)} of {len(rows)} records -> {args.out}")


if __name__ == "__main__":
    main()
