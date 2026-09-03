"""Rebuild the FTS5 full-text search index (standalone script).

Usage (from the project root):
  .venv/bin/python scripts/build_fts_index.py            # rebuild the default database
  .venv/bin/python scripts/build_fts_index.py --database data/database/catalog.sqlite

Note: the normal build_database() already builds the FTS index; this script is
for a manual rebuild (e.g. a full reindex after upgrading the normalize pipeline).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running directly from the project root (scripts/ subdirectory mode)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.catalog.database import DEFAULT_DATABASE, connect  # noqa: E402
from backend.catalog import search  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="重建 FTS5 全文检索索引")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()

    conn = connect(args.database)
    try:
        start = time.time()
        count = search.rebuild_index(conn)
        elapsed = time.time() - start
        print(f"FTS 索引重建完成：{count} 条记录，耗时 {elapsed:.1f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
