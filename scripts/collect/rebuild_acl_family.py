#!/usr/bin/env python3
"""Re-collect the ACL-family venues with the fixed text_content parser.

Fixes the "O cto T ools" title whitespace corruption (ACL/EMNLP/NAACL, ~2,855
records) by re-parsing the official Anthology XML with the block-aware join.
Also refreshes list_status / verification_status / fetched_at.

Usage: python scripts/collect/rebuild_acl_family.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.collect.build_catalog import (  # noqa: E402
    Fetcher, scrape_acl, write_catalog, validate,
)

# (venue, year, anthology collection)
JOBS: list[tuple[str, int, str]] = [
    ("ACL", 2023, "2023.acl"),
    ("ACL", 2024, "2024.acl"),
    ("ACL", 2025, "2025.acl"),
    ("ACL", 2026, "2026.acl"),
    ("EMNLP", 2023, "2023.emnlp"),
    ("EMNLP", 2024, "2024.emnlp"),
    ("EMNLP", 2025, "2025.emnlp"),
    ("NAACL", 2024, "2024.naacl"),
    ("NAACL", 2025, "2025.naacl"),
]


def main() -> int:
    fetcher = Fetcher(Path("/tmp/aipaperbase-catalog-cache"))
    ok, failures = 0, []
    for venue, year, collection in JOBS:
        try:
            papers = scrape_acl(fetcher, venue, year, collection)
            if not papers:
                print(f"[WARN] {venue} {year}: 0 papers — skipping write")
                failures.append((venue, year, "empty"))
                continue
            path = write_catalog(papers)
            issues = validate(papers, path)
            errs = [i for i in issues if i["issue"].startswith(("missing", "duplicate"))]
            status = "OK" if not errs else f"{len(errs)} errors"
            print(f"[{status}] {venue} {year}: {len(papers)} papers → {path.name}  (validate issues: {len(issues)})")
            if errs:
                failures.append((venue, year, errs[:1]))
            else:
                ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {venue} {year}: {type(exc).__name__}: {exc}")
            failures.append((venue, year, str(exc)))
    print(f"\nDone: {ok}/{len(JOBS)} rebuilt, {len(failures)} failed")
    for venue, year, reason in failures:
        print(f"  - {venue} {year}: {reason}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
